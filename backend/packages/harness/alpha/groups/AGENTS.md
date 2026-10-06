# `packages/harness/alpha/groups/`

The group-deliberation subsystem: the staged war room, the group runner, the
supervisor, bot lifecycle operations, the capability-acquisition fence,
claim-level consensus, cross-agent taint screening, and the auto-trigger.

## Two dispatch planes, not one

| Table | Feeds | Surface |
| :--- | :--- | :--- |
| `commands/channel_ops.py::CHANNEL_COMMANDS` | `ChannelCommandRouter` | bot-to-bot channel messages (Feishu/Slack/Telegram/Discord/DingTalk) |
| `commands/catalog.py::get_default_catalog_entries()` | interactive `command_registry` | `/help`, `/commands` in a user chat |

`war-room`, `hire`, `clone`, `rescope`, `archive` and `acquire` are in
`CHANNEL_COMMANDS` only, and that is correct. Copying them into the catalog would
add interactive rows with no interactive handler, so each would take the
registry's documented placeholder path and advertise a command that does
nothing. Tests: `tests/test_war_room_honesty.py`.

## The war room (`groups/war_room.py`)

A **scheduler over existing engines**, not a second deliberation system. It owns
timing, isolation, quorum, persistence, taint screening and governance; it owns
no reasoning of its own.

What it composes:

- **Quorum** — `groups/quorum.py::QuorumEngine`. `required_votes()` in
  `WarRoomConfig` is the single place a policy becomes a number, so the enforced
  threshold and the reported threshold cannot drift. `abstain` is a real fourth
  choice, counted rather than discarded.
- **Participants** — real subagents through
  `subagents/executor.py::SubagentExecutor`, the same seam
  `groups/runner.py` uses. `SubagentParticipant` is the runtime binding.
- **Timing** — `channels/timing.py` (`StageBudget`, `Deadline`, `run_bounded`,
  `RoomIntake`) on an injected monotonic clock.
- **Transcripts** — `channels/transcript.py::TranscriptStore`.
- **Governance** — `channels/ledger.py`. Fail-closed: a ledger that refuses the
  room's opening transition means the run never starts.
- **Agreement** — `groups/consensus.py`.
- **Taint** — `groups/taint.py`.

### What it uses from `alpha.deliberation`, and what it does not

It imports the `DeliberationStrategy` contract and, for `strategy="auto"`, the
`DeliberationRouter` classifier. `plan_stages()` gives every strategy a real
stage shape; `resolve_strategy()` carries the router's rationale onto the run.

It does **not** execute the multi-model engines. A `council` room's
`draft → blind_review → chairman` stages run one subagent each, **not**
`CouncilEngine`; a `red_team` room's `attack` stage does not call
`AdversaryDeliberator` — the strategy decides the *shape* of the deliberation,
not the machinery behind it; the engines are reached through the `deliberate`
tool and `app/gateway/routers/deliberation.py`.
`test_a_deliberation_claim_requires_a_real_deliberation_import` in
`tests/test_war_room_honesty.py` enforces that a docstring crediting
`alpha.deliberation` and the module's real imports cannot disagree.

### Strategy plans

All ten non-`AUTO` values have a plan. With **no** strategy the room is the
original fixed `positions → cross_exam → synthesis` and is recorded as
`"legacy_fixed"` — never attributed to a strategy it did not use. `DEBATE` is the
only plan whose length varies: `opening → cross_exam ×N → judge`.

### Exploratory stages do not end the room

`StagePlan.requires_previous_quorum` is what makes the plans usable: only the
**last collecting** stage may terminate a run. A debate finding no agreement
after one round is working correctly, a red-team attack is *supposed* to find
something — earlier stages are exploratory and a failed quorum simply continues.
The legacy fixed plan keeps its all-gating behaviour because that is what its
invariants were written against.

### Agreement is measured over claims, not over text

`groups/consensus.py` parses each contribution's `STATED CLAIMS` line (reusing
`alpha.deliberation.parsing`) and builds a **co-agreement graph**: members
sharing at least one claim form a component, and the largest is the room's
position.

Four buckets, and the distinction between the last two matters:

| Bucket | Meaning |
| :--- | :--- |
| `agreeing` | in the largest component (size ≥ 2) |
| `dissenting` | in a *different* component of size ≥ 2 — they agree against the room |
| `isolated` | nobody else shares their position; they did not oppose anyone |
| `unparsed` | stated nothing parseable; excluded from every side |

**Self-reported confidence is recorded and never used to move a threshold.**
Weighting on it would be theatre. **Correlated voters** (the same `model_id`
twice) collapse to one voice, disclosed rather than silently shrinking the room.
The **minority view is preserved verbatim** on the run.

### Cross-agent taint

A war room is structurally the topology prompt-injection research targets: one
agent's output is rendered into the next agent's prompt on purpose — how an
injected instruction propagates. `groups/taint.py` makes that visible and bounded
rather than claiming to stop it:

- `screen()` classifies each contribution clean/suspect/infected and says why.
  A room *discussing* injection is not flagged; only an unmarked hit inside an
  illustrative clause is dismissed, and that dismissal is itself recorded.
- `sanitize_for_prompt()` replaces a detected span with a visible
  `[redacted: <category>]` marker before the text re-enters another participant.
  The receipt keeps the full contribution — the transcript is evidence.
- `room_is_colluding()` flags a suspiciously unanimous room.
- **Enforcement:** an unaddressed infection downgrades a run from `succeeded` to
  `partial`. Transparency without enforcement is what let a corrupted result ship
  in the published multi-agent collusion incident. A pattern list is a
  mitigation, not a guarantee; the honest claim is "screened and disclosed".

### The five invariants

Each has a test in `tests/test_war_room_deliberation.py`:

1. **A wedged participant cannot hold the room open.** Every stage is bounded by
   its own `timeout_seconds + grace`; each participant gets a *slice* of the
   stage budget so one slow agent cannot starve its siblings. Queue time is
   inside the slice and recorded as `slot_timeout`, distinct from an agent that
   wedged after getting a slot.
2. **One member's failure never degrades a sibling.** `gather(...,
   return_exceptions=True)` plus a `_contribute` that cannot raise.
3. **Synthesis fails loudly on empty output.** Empty synthesis is
   `status=FAILED, synthesis=None`.
4. **A transcript fault cannot rewrite a delivered verdict.** Append failures
   land in `transcript_errors`; an unpersistable receipt is escalated to
   `receipt_loss` and downgrades `succeeded` to `partial`.
5. **Quorum is explicit, configurable and reported** — `all`/`any`/`majority`/
   `supermajority`, with the tally and `engine_agrees_with_policy` recorded.

A run always reaches a terminal status. `QuorumEngine` is in-memory: proposals
and votes die with the process; only `run.json` and `transcript.jsonl` survive.

### The kill switch

`_kill_switch_engaged()` reads `bots/kill_switch.py` and treats an *unreadable*
switch as **engaged**, not as off. A long run polls it: the switch is
process-local module state with no notification channel.

## The auto-trigger (`groups/trigger.py`)

Decides whether a turn should open a room — mostly vetoes, since an agent that
convenes a panel on every turn is worse than one that never does. Reuses
`DeliberationRouter` so a room and a council never disagree about prompt
difficulty, then applies gates a classifier cannot know about.

Default off. Never in a non-interactive turn. Cooldown, duplicate reuse, a
per-turn cap, `red_team` reserved for humans, and **a named `gate` and
`rationale` on every refusal** — a silent skip is indistinguishable from a bug;
a router fault fails closed. Decision function with a pre-flight surface
(`war_room action=evaluate`, `POST /api/war-rooms/evaluate`); **nothing calls it
on a conversation's behalf yet** — the room still opens only when a model calls
the tool.

## The `war_room` tool

`tools/builtins/war_room_tool.py` is the model-facing door. Its action `Literal`
is a **closed set** and every member is handled in the dispatcher — enforced by
`test_every_advertised_action_is_handled_in_the_dispatcher`, because
`action="status"` once existed in the `Literal` with no branch and returned
"unknown action" for a schema-valid call.

| Actions | Owner |
| :--- | :--- |
| `open`, `status`, `evaluate` | `groups/war_room.py`, `groups/trigger.py` |
| `hire`, `clone`, `rescope`, `archive`, `unarchive`, `revalidate` | `groups/lifecycle_ops.py` |
| `acquire` | `groups/acquisition.py` |

`open` takes `strategy` and `debate_rounds`; omit `strategy` for the legacy
fixed room. `open` goes through `_run_off_loop`, never `asyncio.run` directly:
the tool runs inside the agent's event loop where `asyncio.run` raises; the
helper uses the same daemon-thread fallback as `swarm_tool.py`.

Every action is fail-closed. Lifecycle actions run under a **non-negotiable
authority ceiling**: a bot may create, clone, re-scope and archive other bots,
but may not widen its own authority, edit the ceiling, or mint a profile
exceeding it. Archiving never deletes and is reversible. Acquisition is a
fenced supply chain — untrusted by default, quarantined, provenance recorded,
scanned, approved by somebody other than the requester.

## Read side, and the REST surface

`commands/channel_ops.py::runtime_root()` resolves to
`$AGENT_WORKSPACE_HOME/war_room` (else `runtime_home()/war_room`, else
`cwd()/.alpha/war_room`). Per run:

```
<root>/<room>/<run_id>/run.json           # atomic replace, the run record
<root>/<room>/<run_id>/transcript.jsonl  # gap-free, sequence-checked
```

`list_persisted_runs()`, `load_run_record()` and `read_transcript_tail()` back
both `action="status"` and `app/gateway/routers/war_rooms.py`
(`GET /api/war-rooms`, `/{run_id}`, `/{run_id}/transcript`, `/analytics`,
`/trigger-policy`, `POST /evaluate`). An unparsable `run.json` is reported as
`status="unreadable"` with the error, never silently dropped; a partial trailing
transcript line is ignored rather than raised.

The router never labels a run **verified**: it reports
`consensus_supported` / `consensus_degraded` / `tainted` / `unverified`, because
a quorum established that agents agreed, not that they were right. The frontend
surface is the **Deliberation** view
(`frontend/src/components/sections/WarRoomRunsSection.tsx`), a *separate* view id
from the pre-existing `warroom` view, which belongs to the enterprise platform.

## Live activity and work claims (`groups/activity.py`, `groups/claims.py`, `groups/coordination.py`)

`presence.py` answers **who is enrolled and what lifecycle word their registry
row holds**; these three answer **what is each agent doing right now**, a
different question that was previously unanswerable.

### Why a crashed agent used to look idle

`presence._resolve()` reads only BotRegistry `status`/`last_active` plus
attendance — **neither is told a process died** — so a crashed agent and a
cleanly finished one both rendered `idle`. The separating evidence already
existed durably and nothing read it: `RunManager` persists a named stop reason
for every crash path (`RECOVERABLE_RUN_STOP_REASONS` = `orphan_recovered`,
`gateway_shutdown`, `model_failure`, `network_waiting`) and `GroupRunService`
rewrites an in-flight row to `interrupted` at load. `presence.py` never touches
the run store. This layer is a **projection of the run lifecycle**, not a second
authority.

### The rule: `working → idle` requires proof

Either a terminal run event or an explicit release. A heartbeat that merely goes
quiet is `unresponsive`, **never** `idle`. That single rule is the fix.

### Two axes, never one enum

`activity` (`working`/`idle`/`blocked`/`unresponsive`/`crashed`/`offline`/`unknown`)
and `health` (the `alpha.bots.health` verdict) are separate fields: a bot can be
`blocked` and `healthy` at once, and collapsing them is how one word came to
answer two questions.

### `unresponsive` is not `crashed`

Heartbeat silence is **evidence of a problem, not proof of death** — an agent
inside a twenty-minute tool call is silent too. Only a named terminal reason (or
loss of run ownership) earns `crashed`; a UI that paints both red re-creates the
original lie one layer up, so `tone_for()` gives them different tones.

### Precedence inside `derive_activity`, which is a contract not an implementation

1. An administrative lifecycle word (`suspended`/`archived`) outranks everything —
   a suspended bot was *told* to stop; that is not a death.
2. A hard run fact outranks a stale heartbeat.
3. An **explicit self-report outranks inferred liveness.** `blocked` and `idle`
   claim the *absence* of effort, so silence confirms them; `working` claims live
   effort, so silence retracts it into `unresponsive`.
4. Only then does silence apply, and as `unresponsive`.

Every derived state carries `evidence{reason, detail, source, run}`: a state this
module cannot explain does not exist in it.

### Signals, and why they are event-driven

The primary heartbeat is the **run lifecycle**, not agent self-reporting: a
crashed agent cannot report its own crash, and a heartbeat costing a model call
gets skipped under load. `RunManager` gained a module-level read-only observer
(`set_activity_observer`), notified at run start, every terminal transition,
ownership loss and orphaned-run recovery. It cannot change a transition and its
exceptions are swallowed — a display bug must never fail a run.
`app/gateway/deps.py` installs the binding; outside the Gateway it is `None` and
costs one global lookup.

`POST /{name}/activity/heartbeat` exists only for phase detail the run store
cannot know ("refactoring the router").

### Reconciliation is on read

Matching `crew.ensure_crew()` and `LockManager.sweep_expired()`. No new
supervisor loop, so **no capability count moves** - 136 tools / 66 routers / 44
middlewares / 9 loops / 118 engines all stay correct.

### Claims are a separate store from locks, on purpose

| | Work claim | Resource lock |
| --- | --- | --- |
| guarantee | intent, visible to peers | exclusive ownership |
| on overlap | soft conflict, nothing refused | `LockConflictError`, HTTP 423 |
| lifetime | 120s, renewed by pulse | TTL 1800s |
| holder crashes | `orphaned` — available to take | blocks until TTL lapses |

Two guarantees, two stores; one boolean would force one column to be a lie.
**A claim never refuses** — enforcement stays in `locks.py`, where a wrong block
already has a reviewed shape.

This also fixes a live gap: `projects/conflicts.py::detect_lock_conflicts()` hunts
for two live same-path locks from different owners, but `LockManager.acquire()`
*raises* on exactly that, so the state is unreachable through the public API
(`tests/test_projects_coordination.py` inserts the second lock into a private dict
to simulate it); claims are non-refusing, so the overlap is reachable and worth
reporting.

`normalise_subject()` collapses `./a.py` and `a.py` to one spelling — neither
store normalised before, so a claim and a lock could disagree about the same file.
Dir-covers-children reuses `locks.py`'s exact rule.

### The crash seam, and why a transfer is never automatic

`coordination.crash_orphans()` moves a **hard-crashed** agent's claims from held
to `orphaned`. Two rules:

- Only a *hard* crash verdict triggers it — an `unresponsive` agent may be mid
  tool-call, and taking its claims would hand live work to a second agent.
- Nothing is transferred. **The crash is evidence; the transfer is a decision.**
  Reclaiming is an explicit `POST /{name}/claims/{id}/reclaim`, and only an
  *orphaned* claim may be reclaimed — a live one is a 409, never a silent steal.

### Communication reuses the transcript

Conflict and crash news post into the room using `MessageIntent` values that
already exist (`status`, `warning`, `blocker`, `handoff`); a sixth messaging
mechanism would be worse than none. `post_message()` resolves next speakers, so a
`@mention` in a conflict warning actually reaches the orchestrator.

### Invariants this layer must not break

- `GroupRoom.members` is **crew-owned**. Never write activity or claims there.
  Membership is resolved through `effective_members()`.
- `direct_count` and `effective_count` always travel together; `by_activity` is a
  breakdown summing to `count` and is kept out of them.
- Claims live beside rooms, never in the roster.
- Attribution is disclosed: `explicit` (server-stamped) vs `roster_match`
  (inferred from resolved membership). A run with neither binding is attributed to
  no room.

### Routes (declared above the `/{name}` catch-all)

`GET /{name}/activity`, `GET /{name}/activity/{bot}`,
`POST /{name}/activity/heartbeat`, `POST /{name}/activity/reconcile`,
`GET|POST /{name}/claims`, `DELETE /{name}/claims/{id}`,
`POST /{name}/claims/{id}/reclaim`. Refusal shapes stay distinct: a missing claim
is 404, a live un-reclaimable claim is 409, a non-member is 404.

Tests: `tests/test_group_activity.py`, `test_group_activity_ledger.py`,
`test_group_claims.py`, `test_group_activity_routes.py`,
`test_run_activity_observer.py`, `test_group_chat_actions.py`.

## Advanced messaging (`service.py` additions, 25 routes above `/{name}`)

The group profile, its links, goals, project binding, and clone live on
`GroupRoom` as **optional fields with defaults**, so an older room round-trips
unchanged; the persistence envelope carries `version: 3` (was `2`). A field that
defaults is not a field that is absent — `GET /{name}/profile` always answers
with every key present.

| Concern | Routes | Service |
| --- | --- | --- |
| Identity | `GET\|PATCH /{name}/profile` | `update_group_profile` |
| Links | `GET\|POST\|DELETE /{name}/links`, `PATCH /{name}/links/reorder` | `add_link` / `remove_link` / `list_links` / `reorder_links` |
| Goals | `GET\|POST /{name}/goals`, `PATCH /{name}/goals/{goal_id}` | `add_goal` / `update_goal` / `list_goals` |
| Project binding | `GET\|POST\|DELETE /{name}/project-link` | `link_project` / `get_project_link` / `unlink_project` |
| Clone | `POST /{name}/clone` | `clone_group` (charter only — **never** the transcript) |
| Pinning | `POST\|DELETE /{name}/messages/{id}/pin`, `GET /{name}/pinned` | `pin_message` / `unpin_message` / `pinned_messages` |
| Threading | `GET /{name}/messages/{id}/thread`, `GET /{name}/threads` | `thread_replies` / `thread_roots` |
| Search | `GET /{name}/search` | `search_messages` |
| Receipts | `POST /{name}/messages/{id}/read`, `GET /{name}/unread`, `GET /{name}/messages/{id}/readers` | `mark_read` / `unread_messages` / `message_readers` |
| Typing | `POST\|GET /{name}/typing` | `set_typing` / `typing_indicators` |
| Stream | `GET /{name}/events` | `app/gateway/group_events.py` |

The stream is a **projection of room state, never a second write path**: each
subscriber owns an asyncio queue bounded at `RETAINED_EVENTS = 100`, and
`publish()` drops the *oldest* frame rather than blocking, so a client that stops
reading can neither grow the process's memory nor stall `post_message`. A dropped
frame is a re-read of the room, not a lost fact. It crosses into the app layer
through `set_group_event_observer` (installed by `app/gateway/deps.py`), so the
harness holds an optional callable instead of importing `app.*`; a Gateway that
never installs one degrades to "no live stream", not to an import failure.

The bus is keyed by **`room_id`, never by room name**. Every publisher passes
`room.room_id` (`post_message`, link, pin and goal writes alike), so
`GET /{name}/events` resolves the room and subscribes with `room.room_id`.
Subscribing under the caller's name instead produced a stream that answered
200, declared `text/event-stream`, held the connection open and heartbeated on
schedule — and delivered no event for as long as the client stayed connected.
History replay filters on the same key, so a late subscriber missed the backlog
too. `GroupEventBus` tests passing invented keys like `"room_x"` only prove the
bus matches a key to itself; the invariant worth pinning is a *route* subscriber
receiving what a real `post_message` publishes.

Five things that must not regress — the first three are invariants, the last two
are facts an API caller will otherwise discover the hard way:

- **Route order is load-bearing.** Every literal path above is declared before
  the `/{name}` catch-all. Swapping two of them makes Starlette answer
  `Room 'pinned' not found` for `GET /pinned`, which is indistinguishable from
  an absent room.
- **`mark_read` writes.** Receipts are read-modify-write against a per-room
  file; a path that returns the updated message without `_save()` reports a
  read that never happened. This was a real defect — the receipt file showed
  `"receipts": {}` after a successful 200 — and
  `test_group_advanced_messaging.py` pins persistence, not just the response.
- **Search over a deleted message must not find it.** The index is derived
  from the live transcript, so `delete_message` removes it; a search test that
  never deletes is not a search test.
- **There are two things called `goals`, and they are different stores.**
  `PATCH /profile`'s `goals` writes `GroupRoom.goals`, a flat `list[str]` that
  `GET /profile` reads back as `profile.goals`. `GET|POST /goals` reads
  `GroupChatService._goals[room_id]`, a separate table of records with
  `goal_id`/`status`/`progress`. The envelope's top-level `goals` key is the
  *tracked* table; the room record's `goals` field is the *charter*. They do
  not sync, and the UI renders only the tracked ones — so a client that reads
  `profile.goals` and reports "this group has no goals" is reading the wrong
  field, not observing an empty room.
- **`clone_group` copies the charter, not the state.** Profile, links,
  membership and rules are copied as fresh records with new ids so the clone
  cannot mutate the source; `project_id` is reset to `None`. The transcript,
  tracked goals, pins, receipts and the project binding are **not** copied —
  a clone that started with a project binding would be doing work nobody asked
  it to do. `CloneRequest` exposes `include_members` / `include_rules` /
  `include_links` / `include_profile` so a caller can opt out of each layer.

### Notifications (`alpha/notifications/`, 7 routes)

`GroupChatService.post_message` calls `_raise_notifications()` inside the
**single write path**, so a message that reached the room always raises its
notification and one that did not can never claim to have been announced. The
package owns three things: the per-operator JSON store (bounded at
`MAX_HISTORY = 500`, fail-closed reads), the trigger table, and the preference
model.

- **Harness never imports app.** The Gateway installs the sink through
  `set_group_event_observer(...)` from `app/gateway/deps.py`; the harness side
  is an optional callable. An `app.*` import here fails
  `tests/test_harness_boundary.py`.
- **Single-process, single-operator, said out loud.** The store is one JSON
  file per user under `Paths.user_dir(owner)`; the router serves the operator id
  rather than inventing multi-user isolation. It is restart-recoverable, not
  cross-process exactly-once.
- **The operator is on every audience, and that is not optional.** The id lives
  once, as `alpha.notifications.OPERATOR_USER_ID`, because the trigger path
  decides *who to notify* and the read path decides *whose inbox to serve*.
  `_raise_notifications()` therefore appends the operator to the resolved
  roster, so the record lands somewhere a human can open it. A room's roster is
  agents; `on_message_posted` skips only the *sender*, which is what keeps the
  operator from being told about their own post once they are on that list.
  This is the third bug of the same shape this layer has had: with the audience
  as the roster alone, every notification was created, durable and counted, and
  the bell was structurally empty because the only reader asked for a user
  nobody had written to. Tests that read back an *agent's* inbox pass straight
  through it — read back the one the UI asks for.
- **A message carries no hard mute.** `sound` is `True` on a room message and
  the operator's `sound_enabled` plus quiet hours decide delivery. It used to be
  `intent in _LOUD_INTENTS`, which excluded the default `discussion` intent, so
  an agent's ordinary post badged the panel and stayed inaudible while every
  intent that rang already carried a raised priority — and a record-level
  `False` is invisible to every preference the client evaluates, so nothing in
  the UI could explain or override it.
- **Muting deletes nothing.** Preferences gate *delivery* (sound, desktop
  popup); every record still lands in history and still counts. Quiet hours
  silence the chime without suppressing the record.

Tests: `tests/test_group_advanced_messaging.py`,
`tests/test_group_notifications.py` (87 cases together). Frontend:
`frontend/src/lib/groups-profile.test.mjs`, `frontend/src/lib/notifications.test.mjs`,
`frontend/src/lib/groups-profile-model.ts` and `notifications-model.ts` (pure
derivations, no imports). `GroupProfilePanel.tsx` is a drill-down on one group
and stays inside `MessagesSection`; `NotificationsBell.tsx` is mounted in
`components/chat-shell/WorkspaceTopBar.tsx`, because mounting it is what runs
`primeNotificationAudio()` and the 20s poll — inside the Messages section header
the agent's message was recorded and announced nowhere on the chat view. Both
facts are pinned by `src/lib/notifications.test.mjs`.

## Automatic write claiming (`groups/write_watch.py`)

The claim store shipped with the right schema and an acquisition nobody could
rely on: an agent had to call `group_chat action=claim`, and **an agent that
forgets collides anyway** — forgetting is what happens when two agents run at
once.

Every external implementation agrees on the acquisition point — a `PreToolUse`
hook on the edit tool. `ReadBeforeWriteMiddleware` wraps **exactly**
`{write_file, str_replace}` and already holds both the path and a
per-`(scope, path)` lock, so it is the insertion point: one place, both tools,
inside the critical section the gate already takes.

- The claim is taken **before** the write, so a peer polling mid-write sees it.
- The warning rides the result of the call that **actually ran**, so a model
  reading its transcript sees the collision attached to a write it really
  performed. A `Command` is not rewritten — appending to the wrong surface would
  corrupt it, and the claim is recorded either way.
- It **warns, never blocks** in an `advisory` room (the default). Refusal stays
  in `projects/locks.py`, where a wrong block already has a reviewed shape
  (423 + holder); blocking by default would mean the mechanism that draws a
  status dot could lose work.
- Under the project's `lock_policy: "strict"` it refuses: `enforce_write()`
  consults `StrictEnforcer` before the handler runs, and a live claim by
  another agent produces an error `ToolMessage` stamped with `WRITE_BLOCK_KEY`
  plus the enforcer's verdict — the handler never runs, and a confirmed-dead
  holder never refuses. Tests: `tests/test_group_write_watch.py`.
- Identity comes from **server-owned runtime context**, never tool arguments the
  model controls. A run with no room binding records nothing — a real write with
  no known crew.

The warning deliberately does **not** say the other agent "is writing": a live
claim is a 120s lease on *declared intent*, not proof of an in-flight edit, and
overstating it is the same class of error this feature exists to remove.

## Process liveness (`groups/liveness.py`)

Closes the one case the run store structurally cannot see: the agent's process
is gone and nothing wrote a terminal status, **because the write itself is what
died**.

`pid_alive()` is **tri-state**: `None` means "nothing measured", never `False`.
Collapsing the two would orphan the entire historical claim set on the first
sweep, since records written before pids were captured have none.

Liveness yields `unresponsive`, **never** `crashed`, and is consulted **last** —
below an administrative lifecycle word, a hard run fact and plain silence. It is
corroboration, not a cause: pids are recycled, so a gone pid plus a stale
heartbeat is good evidence work was abandoned, weak evidence it "crashed";
`holder_is_gone()` requires **both** signals.

The asymmetry is deliberate: orphaning may act on soft evidence (a false orphan
costs one wasted read), the status display may not (a false crash is a
fabricated death in the operator's UI), so `crash_orphans()` reports `crashed`
and `process_gone` separately with different wording.

## Write sets (`claims.py::declare_write_set`)

A refactor across twelve files was twelve claims and twelve messages, and a peer
polling between them sees a partial picture — the worst moment to coordinate
from. `declare_write_set()` declares the whole scope in one call and announces
it once, turning "is agent X editing file Y" into "is agent X working in this
area". Every subject is claimed on identical terms: a set is N claims plus one
announcement, **not a weaker guarantee**. Bounded at 20 subjects and 25 per bot.

## Strict enforcement (`groups/enforcement.py`)

`lock_policy` has accepted `"strict"` since it was written and **nothing has
ever read it** — the most fully decorative setting in the collaboration config,
worse than absent: an operator who sets it believes writes are protected.

This module reads the policy and returns a decision; the refusal shape stays
consistent with existing write-gate blocks — this supplies the policy
question, not a second enforcement path. The write-claiming path
(`write_watch.enforce_write`, called by `ReadBeforeWriteMiddleware` on
`write_file`/`str_replace`) is its production consumer: under `lock_policy:
"strict"` a write that lands on another agent's live claim is refused before
the handler runs; advisory rooms are unchanged. Two limits, because an over-eager enforcer is worse
than none:

- **It refuses unless the holder is CONFIRMED gone.** The question is never "is
  there evidence the holder is alive?" but "is there evidence it is *dead*?" —
  blocking protects real work, allowing destroys it, so `unresponsive` and
  `unknown` both block: silence is not death, but not consent either, and a
  mid-tool-call agent is `unresponsive` and genuinely working.
- **It sweeps before deciding**, so a decision never targets a lapsed lease.

It walks the room's live claims directly rather than `detect_soft_conflicts()`,
which needs *two* holders by construction — but strict mode exists for exactly
ONE other agent holding the file: not a "conflict" to anybody, a collision
waiting to happen. `crashed_lock_ttl()` shortens a confirmed-dead holder's lock
so a crash does not block a path for the rest of the 1800s TTL — not zero: the
crash is evidence, and a peer wanting the file should take it deliberately.

## UI (`frontend/src/components/sections/GroupActivityPanel.tsx`)

Rendered inside the group details pane, below the member list: a **third read**
alongside roster and presence dots, settling separately — an older Gateway has
no `/activity` route, which must read as "this build does not have it", not
"nobody is working". It rides the existing 10s poll rather than starting its
own timer: one cadence, one place, no second interval to leak on unmount.

`group-activity-view.test.mjs` pins the *rendering* honesty the pure
derivations cannot catch: a crashed agent's row is never hidden, `crashed` and
`unresponsive` are never painted the same colour, and the refresh control
disables itself in flight.

## Known gaps

- The strategy plans do not **execute** the deliberation engines.
- No convergence-based early stop; a debate runs every round it was asked for.
- The auto-trigger is not wired into the conversation loop.
- No bus events, OTel spans, or `RunJournal` integration — a run is observable
  only by reading its persisted state.
- `QuorumEngine` is not persisted; votes die with the process.
- No resume of an interrupted room, no priority queue, no global concurrency cap.
- Participants are undifferentiated: `war_room_tool.open` maps every roster name
  to the same `SubagentParticipant()` (`general-purpose`), so agents differ only
  by the `@name` in the prompt. Only the moderator is specialised
  (`agent_type="architect"`).
- `GroupRoom`/`GroupOrchestrator` (`room.py`, `orchestration.py`) are a separate
  group-chat abstraction with five orchestration modes; the war room does not
  use them.

## Tests

`tests/test_war_room_deliberation.py` (invariants, timings, quorum policies),
`tests/test_war_room_strategies.py` (strategy plans, resolution, exploratory
gating, backwards compatibility),
`tests/test_war_room_consensus_trigger_taint.py` (consensus, taint, trigger),
`tests/test_war_rooms_api.py` (the REST surface under failure),
`tests/test_war_room_reachability.py` (production import edges; no module wired
only by a test), `tests/test_war_room_honesty.py` (claim/import coherence, the
closed action set, the two command planes, loop-safety).
