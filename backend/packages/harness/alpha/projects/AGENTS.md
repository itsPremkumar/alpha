# `alpha.projects` — project workforce layer

Agents are reusable workers; projects are shared workspaces; assignment is a
temporary relationship (`membership.py`). One canonical state per project is
folded from the append-only event bus (`events.py` -> `state.py`).

## Modules

| Module | Owns |
|---|---|
| `crew.py` | **the crew**: reconciles membership + group room + shared memory as one unit |
| `membership.py` | join/leave/heartbeat/presence, file-backed roster |
| `locks.py` | file/dir/task/artifact locks, TTL expiry, access requests |
| `constitution.py` | `PROJECT_CONSTITUTION.md` validation (11 sections), hash pinning |
| `events.py` | append-only per-project `events.jsonl` bus + search |
| `state.py` | canonical snapshot folded from the bus, lifecycle phases |
| `decisions.py` | ADR log + search (project memory) |
| `context.py` | role-filtered context snapshots with char budgets |
| `memory_bridge.py` | folds the group transcript into Level-2: digest, briefs, open questions, pending mentions, compaction |
| `routing.py` | capability -> availability -> load -> reputation selection |
| `workspace.py` | standard layout + git worktree leases (via `sandbox.worktrees`) |
| `handoffs.py` | project-scoped handoff records (wraps `bots.handoff`) |
| `evidence.py` | DONE-gate evidence checks (pure functions) |
| `goals.py` | durable goal-tree artifact with attempts + progress |
| `conflicts.py` | lock-overlap detection, raise/resolve with ADR recording |

## Crew invariants (`crew.py`)

`crew.py` is the only thing allowed to reconcile membership against the group
room. Route every attach/detach/read through it — calling `membership` or
`groups` directly re-introduces the drift it exists to prevent.

- `room.members` mirrors `membership.presence()`; **membership is authoritative**.
  A bot in the room but not on the crew is stale and gets removed.
- A room exists **iff** the crew has 2+ agents. Dropping to 1 *parks* the room
  (history preserved) rather than deleting it.
- Leaving releases the departing agent's locks.
- Every reconciliation that changed something emits `room_synced`; a settled crew
  emits nothing, so polling `ensure_crew()` is cheap.
- Collaboration policy lives in `project-config/collaboration.json` and is
  re-applied to the room on every read.
- `ensure_crew()` also runs a compaction pass, so a long-running crew keeps its
  Level-2 memory bounded. Compaction is **non-destructive**: overflow is
  summarised into `context.json` and `room.log` is never truncated.
- Compaction bookkeeping reads `context.json` **directly**. Do not route it
  through `get_project_memory()` — that now includes these digests and would
  recurse.

## Rules

- Storage is file-backed JSON/JSONL under `runtime_home()/projects/` (same
  atomic tmp+replace pattern as `bots/registry.py`). No DB migrations.
- `events.py` types are additive; consumers ignore unknown types.
- Never import `app.*` (harness boundary, enforced by
  `tests/test_harness_boundary.py`).
- Every module ships with `backend/tests/test_projects_<module>.py`.
