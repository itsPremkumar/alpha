# `packages/harness/alpha/groups/`

The group-deliberation subsystem: rooms, the staged war room, the group runner,
the supervisor, bot lifecycle operations, and the capability-acquisition fence.

## Two dispatch planes, not one

`groups/` is reachable from two unrelated command surfaces, and conflating them
is a known defect:

| Table | Feeds | Surface |
| :--- | :--- | :--- |
| `commands/channel_ops.py::CHANNEL_COMMANDS` | `ChannelCommandRouter` | bot-to-bot channel messages (Feishu/Slack/Telegram/Discord/DingTalk) |
| `commands/catalog.py::get_default_catalog_entries()` | interactive `command_registry` | `/help`, `/commands` in a user chat |

`war-room`, `hire`, `clone`, `rescope`, `archive` and `acquire` are in
`CHANNEL_COMMANDS` only, and that is correct. Copying them into the catalog
would add interactive rows with no interactive handler, so each would take the
registry's documented placeholder path and advertise a command that does
nothing. Tests: `tests/test_war_room_honesty.py`.

## The war room (`groups/war_room.py`)

A **scheduler over existing engines**, not a second deliberation system. It owns
timing, isolation, quorum, persistence and governance; it owns no reasoning of
its own.

What it composes:

- **Quorum** — `groups/quorum.py::QuorumEngine`. `required_votes()` in
  `WarRoomConfig` is the single place a policy becomes a number, so the enforced
  threshold and the reported threshold cannot drift.
- **Participants** — real subagents through
  `subagents/executor.py::SubagentExecutor`, the same seam
  `groups/runner.py` uses. `SubagentParticipant` is the runtime binding.
- **Timing** — `channels/timing.py` (`StageBudget`, `Deadline`, `run_bounded`,
  `RoomIntake`) on an injected monotonic clock.
- **Transcripts** — `channels/transcript.py::TranscriptStore`.
- **Governance** — `channels/ledger.py`. Fail-closed: a ledger that refuses the
  room's opening transition means the run never starts.

### What it does and does not use

`groups/war_room.py` imports `DeliberationStrategy` from
`alpha.deliberation.models` — the enum, nothing heavier — and uses it to shape
the room. `plan_stages()` gives every strategy a real stage plan, and
`resolve_strategy()` routes `auto` through
`alpha.deliberation.router.DeliberationRouter`, carrying the router's own
rationale onto the run.

What it still does **not** do is *execute* the deliberation engines. A
`council` room's `draft → blind_review → chairman` stages run one subagent per
participant each; they do not call `CouncilEngine`, and a `red_team` room's
`attack` stage does not call `AdversaryDeliberator`. The strategy decides the
*shape* of the deliberation, not the multi-model machinery behind it. The
engines themselves are reached today through the `deliberate` tool
(`tools/builtins/deliberation_tool.py`) and `app/gateway/routers/deliberation.py`.

`DeliberationStrategy` has 11 values (`AUTO`, `SINGLE`, `ENSEMBLE`, `COUNCIL`,
`DEBATE`, `PEER_REVIEW`, `MOA`, `JUDGE`, `RESEARCH_COUNCIL`, `RED_TEAM`,
`EXPERT_PANEL`) and all ten non-`AUTO` values have a plan. With **no**
`strategy`, the room is the original fixed `positions → cross_exam → synthesis`
and is recorded as `"legacy_fixed"` — never attributed to a strategy it did not
use.

`test_a_deliberation_claim_requires_a_real_deliberation_import` in
`tests/test_war_room_honesty.py` enforces that a docstring crediting
`alpha.deliberation` and the module's actual imports cannot disagree. That test
exists because this file's predecessor claimed reasoning came from
`alpha.deliberation` while importing nothing from it.

### Exploratory stages do not end the room

`StagePlan.requires_previous_quorum` is the field that makes the strategy plans
usable. Only the **last collecting** stage may terminate a run. A debate whose
opening round finds no agreement is working correctly, and a red-team attack is
*supposed* to find something — so their earlier stages are exploratory and a
failed quorum simply continues to the next stage. The legacy fixed plan keeps
its original all-gating behaviour, because that is what its existing invariants
were written against. Tests: `tests/test_war_room_strategies.py`.

### The five invariants

Each has a test in `tests/test_war_room_deliberation.py`:

1. **A wedged participant cannot hold the room open.** Every stage is bounded by
   its own `timeout_seconds + grace`; each participant gets a *slice* of the
   stage budget so one slow agent cannot starve its siblings. Queue time for the
   concurrency semaphore is inside the slice and recorded as `slot_timeout`,
   distinct from an agent that got a slot and then wedged.
2. **One member's failure never degrades a sibling.** `gather(...,
   return_exceptions=True)` plus a `_contribute` that cannot raise.
3. **Synthesis fails loudly on empty output.** Empty synthesis is
   `status=FAILED, synthesis=None`. It is never published as `synthesis=""`
   with a success status.
4. **A transcript fault cannot rewrite a delivered verdict.** Append failures
   land in `transcript_errors`; a receipt that cannot be persisted is escalated
   to `receipt_loss` and downgrades `succeeded` to `partial`.
5. **Quorum is explicit, configurable and reported** — `all`/`any`/`majority`/
   `supermajority`, with the tally and `engine_agrees_with_policy` recorded.

A run always reaches a terminal status. There is no path that returns while
`status == running`. `QuorumEngine` itself is in-memory: proposals and votes do
not survive a process restart, only `run.json` and `transcript.jsonl` do.

### The kill switch

`_kill_switch_engaged()` reads `bots/kill_switch.py` and treats an *unreadable*
switch as **engaged**, not as off. A long run polls it, because the switch is
process-local module state with no notification channel.

## The `war_room` tool

`tools/builtins/war_room_tool.py` is the model-facing door. Its action `Literal`
is a **closed set** and every member has a dispatcher branch —
`test_the_war_room_tool_every_advertised_action_refuses_cleanly` and
`test_status_is_in_the_action_literal_and_has_a_dispatcher_branch` enforce that,
because `action="status"` once existed in the `Literal` with no branch and
returned "unknown action" for a schema-valid call.

| Actions | Owner |
| :--- | :--- |
| `open`, `status` | `groups/war_room.py` |
| `hire`, `clone`, `rescope`, `archive`, `unarchive`, `revalidate` | `groups/lifecycle_ops.py` |
| `acquire` | `groups/acquisition.py` |

`open` takes `strategy` and `debate_rounds`. Both are optional: omit `strategy`
for the legacy fixed room. `strategy="auto"` consults the deliberation router
and the run records the router's rationale verbatim.

`open` must go through `_run_off_loop`, never `asyncio.run` directly: the tool
runs inside the agent's event loop, where `asyncio.run` raises. The helper uses
the same daemon-thread fallback as `tools/builtins/swarm_tool.py`, so the two
cannot drift.

Every action is fail-closed and returns a refusal rather than raising into the
agent loop. Lifecycle actions run under a **non-negotiable authority ceiling**:
a bot may create, clone, re-scope and archive other bots, but may not widen its
own authority, edit the ceiling, or mint a profile exceeding it. Archiving never
deletes and is reversible. Acquisition is a fenced supply chain — untrusted by
default, quarantined, provenance recorded, scanned, approved by somebody other
than the requester.

## Runtime layout

`commands/channel_ops.py::runtime_root()` resolves to
`$AGENT_WORKSPACE_HOME/war_room` (else `runtime_home()/war_room`, else
`cwd()/.alpha/war_room`). Per run:

```
<root>/<room>/<run_id>/run.json           # atomic replace, the run record
<root>/<room>/<run_id>/transcript.jsonl  # gap-free, sequence-checked
```

Read side — `list_persisted_runs()`, `load_run_record()` and
`read_transcript_tail()` — is what `action="status"` reports. A `run.json` that
cannot be parsed is reported as `status="unreadable"` with the error, never
silently dropped, and a partial trailing transcript line is ignored rather than
raised: a status view must still work on a room that was killed mid-append.

## Known gaps

- The strategy plans shape the room but do not **execute** the deliberation
  engines: a `council` room's three stages are still one subagent each, not
  `CouncilEngine`. Semantic voting, `minority_dissent`, `consensus_percentage`
  and `candidate_rankings` from `DeliberationResult` are not yet surfaced.
- No convergence-based early stop, so a debate always runs every round it was
  asked for.
- Participants are undifferentiated: `war_room_tool.open` maps every roster name
  to the same `SubagentParticipant()` (`general-purpose`), so agents differ only
  by the `@name` in the prompt. Only the moderator is specialised
  (`agent_type="architect"`).
- `QuorumEngine` is not persisted, and `agree/disagree/amend` is still derived
  from member *status* rather than from a semantic vote over extracted claims.
- No auto-triggering: the room opens only when a model calls the tool. The
  `DeliberationRouter` can classify a prompt, but nothing calls it on a
  conversation's behalf yet.
- No prompt-infection screening between contributions. The `cross_exam` stage
  embeds peer text verbatim into the next participant's prompt, which is
  structurally the multi-agent propagation path that injection research targets.
- `GroupRoom`/`GroupOrchestrator` (`room.py`, `orchestration.py`) are a separate
  group-chat abstraction with five orchestration modes; the war room does not
  use them.
- No frontend surface for war-room runs. `frontend/src/components/sections/WarRoomSection.tsx`
  is the *enterprise* tab (org chart, RFCs, treasury, missions, council) over
  `alpha/enterprise/`; it does not render a deliberation.

## Tests

`tests/test_war_room_deliberation.py` (invariants, timings, quorum policies),
`tests/test_war_room_strategies.py` (strategy plans, resolution, exploratory
gating, backwards compatibility),
`tests/test_war_room_reachability.py` (production import edges; no module wired
only by a test), `tests/test_war_room_honesty.py` (claim/import coherence, the
closed action set, the two command planes, loop-safety).
