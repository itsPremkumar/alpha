# `deerflow.projects` — project workforce layer

Agents are reusable workers; projects are shared workspaces; assignment is a
temporary relationship (`membership.py`). One canonical state per project is
folded from the append-only event bus (`events.py` -> `state.py`).

## Modules

| Module | Owns |
|---|---|
| `membership.py` | join/leave/heartbeat/presence, file-backed roster |
| `locks.py` | file/dir/task/artifact locks, TTL expiry, access requests |
| `constitution.py` | `PROJECT_CONSTITUTION.md` validation (11 sections), hash pinning |
| `events.py` | append-only per-project `events.jsonl` bus + search |
| `state.py` | canonical snapshot folded from the bus, lifecycle phases |
| `decisions.py` | ADR log + search (project memory) |
| `context.py` | role-filtered context snapshots with char budgets |
| `routing.py` | capability -> availability -> load -> reputation selection |
| `workspace.py` | standard layout + git worktree leases (via `sandbox.worktrees`) |
| `handoffs.py` | project-scoped handoff records (wraps `bots.handoff`) |
| `evidence.py` | DONE-gate evidence checks (pure functions) |
| `goals.py` | durable goal-tree artifact with attempts + progress |
| `conflicts.py` | lock-overlap detection, raise/resolve with ADR recording |

## Rules

- Storage is file-backed JSON/JSONL under `runtime_home()/projects/` (same
  atomic tmp+replace pattern as `bots/registry.py`). No DB migrations.
- `events.py` types are additive; consumers ignore unknown types.
- Never import `app.*` (harness boundary, enforced by
  `tests/test_harness_boundary.py`).
- Every module ships with `backend/tests/test_projects_<module>.py`.
