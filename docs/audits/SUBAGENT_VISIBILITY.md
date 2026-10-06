# Why you cannot see a subagent working in the UI

Found 2026-10-06, immediately after shipping the catalog panel (`3de2b19`).
The catalog answers *what a subagent is*. This is about *what a subagent is
doing*, and why the surface that answers that can never show a real one.

**Not a UI bug, and not fixable in the UI.** Two subagent execution paths exist
and they are wired to two different surfaces that have nothing to do with each
other.

| Path | Really executes? | Reaches the UI's live plane? |
| --- | --- | --- |
| `task` delegation — an agent delegates real work | **Yes**, measured | **No**, never registers |
| `POST /api/subagents/control/spawn` — the control plane | **No**, nothing runs it | Yes, registers at `ready` |

So the work is either real and invisible, or the record is visible and no work
ever happens. Neither half of the UI's "Running now" block can be truthful.

## Measured, not inferred

`backend/scripts/probe_control_plane.py` drives the plane directly:

```
before        GET /api/subagents/control -> HTTP 200  []
spawn         POST /api/subagents/control/spawn -> HTTP 200  {"subagent_id":"sub-d0ebc632",...}
after         GET /api/subagents/control -> HTTP 200  rows=1
readback      GET /api/subagents/control/sub-d0ebc632 -> HTTP 200
catalog       GET /api/subagents -> HTTP 200  definitions=8
```

**The plane is wired.** A documented spawn registers, is readable back by id,
and appears in the list. That is the proof that an empty list during ordinary
`task` delegation is a missing writer, not a broken endpoint.

The same run then reads the record it just created:

```
status         'ready'
started_at     None
completed_at   None
last_heartbeat None
result         None
renew_count    0
```

`started_at` is `None` and `last_heartbeat` is `None`. Nothing ran it.

## Root cause, in one sentence

**`SubagentLifecycleManager.start_subagent` has no production caller.**

```
$ grep -rn "manager\.\(spawn_subagent\|start_subagent\|heartbeat_subagent\|complete_subagent\)" backend/ --include=*.py
commands/backend_handlers.py:568:  handle = manager.spawn_subagent(parent_agent_id="lead_agent", contract=contract)
scripts/probe_control_plane.py:8:  (this file's own docstring)
tests/test_bounded_attempt_escalation.py:163,167,182,...
tests/test_unified_handoff_ledger.py:263,264
tests/test_work_resume_after_crash.py:57,58,231
```

Exactly one production call site, and it is `spawn_subagent` from the
`/subagent:spawn` slash command. Every `start_subagent` is a test. A spawned
record is therefore created at `ready`, and nothing transitions it to `running`,
heartbeats it, or completes it.

## Why the obvious fix would be dishonest

The tempting change is to have the `task` tool call `spawn_subagent` for each
dispatch, which would populate the UI immediately.

**That would make the panel worse.** The manager's contract is lease-based:
`SubagentContract` carries `lease_duration_seconds` (60 by default) and a
`token_budget`, and staleness is computed from `last_heartbeat`. The `task`
executor never calls the heartbeat route, so every registered delegation would
show a lease decaying from creation and flip to `stalled` without ever having
been stalled. The UI would render a growing list of confidently wrong rows.

A row claiming `stalled` because nobody wrote a heartbeat is a fabricated
measurement, which is the failure this repository's rules exist to prevent.

## What is actually recorded

Real delegation activity *is* persisted, by a different mechanism:

- `ThreadState.delegations`, captured by `DurableContextMiddleware` before
  summarization can compact the paired tool-call/result messages.
- Run events, which is what the transcript's live activity layer consumes —
  `task_*` custom events from `alpha/tools/builtins/task_tool.py`, rendered by
  `components/SubagentList.tsx`.

So the honest "a subagent is working right now" signal already reaches the
browser through SSE, in the chat view, per run. The three on-disk artifacts in
`AGENT_SELF_SERVICE.md` (123 B, 91 B, 127 B) are that path working; each was
written in the subagent's own thread, and none appeared in
`/api/subagents/control`.

## The two real options, neither a UI-only change

1. **Point the panel at a source that records execution.** Read the delegation
   ledger and `subagent.step` run events for the selected thread and render
   those. Truthful, no backend change, but it is per-thread history rather than a
   live fleet — so the heading "Running now" would have to change to something
   like "This thread's delegations".
2. **Give the lifecycle manager a runner.** Register a real executor that calls
   `start_subagent`, heartbeats on the lease, and completes with a result, and
   have `task` delegate through it. Larger, and the only option that makes a
   genuinely live fleet true. It must not be faked.

Option 1 is a day of UI work and is honest today. Option 2 is a backend feature
and is the real answer to "see a subagent working".

## Not done, deliberately

No runner was invented and no heartbeat loop was written.
`delegate_to_deep_agent` already reports `UNRECOVERABLE_ERROR: "No deep agent
execution backend is configured"`, and this is the same shape one level up: a
control plane with no execution backend behind it. A plausible-looking executor
that does not execute would convert an honest error into a dishonest green row.

No UI was changed for this. The catalog panel (`3de2b19`) reads `/api/subagents`,
which is a definition catalog and is correct.

## Open item: this document is not yet in `docs/INDEX.md`

The repo requires a `FILE_OVERRIDES` entry in `scripts/generate_docs_index.py`
for every new document, and the index must be regenerated from that. Neither was
done here, and the reason is worth recording rather than hiding: while this
document was being written, a concurrent agent's deletion-sensitivity experiment
removed `scripts/generate_docs_index.py` from the working tree, along with
`frontend/src/components/ChatView.tsx`, `frontend/src/components/Composer.tsx`
and `frontend/src/lib/bots.ts`. Committing an index entry now would ship a link
the generator cannot justify and cannot regenerate.

**Nothing was deleted in response.** The missing files are tracked in git at
`HEAD` and belong to the concurrent session. The two actions needed to close
this out, once the tree is stable:

1. Add the `FILE_OVERRIDES` entry for this document.
2. Run `scripts/generate_docs_index.py` and commit `docs/INDEX.md` with it.

## Evidence index

| Artifact | Path |
| --- | --- |
| Probe | `backend/scripts/probe_control_plane.py` |
| Lifecycle manager | `backend/packages/harness/alpha/subagents/lifecycle.py` |
| Only production writer | `backend/packages/harness/alpha/commands/backend_handlers.py:568` |
| UI surface | `frontend/src/lib/subagents.ts::fetchLiveSubagentsStrict` |
| Panel that reads it | `frontend/src/components/sections/SubagentsSection.tsx` ("Running now") |
| Real delegation record | `ThreadState.delegations` via `DurableContextMiddleware` |
| Real delegation events | `alpha/tools/builtins/task_tool.py` → `task_*` SSE → `components/SubagentList.tsx` |
| Delegation that really ran | `docs/audits/AGENT_SELF_SERVICE.md` |
