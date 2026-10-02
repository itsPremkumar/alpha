# `packages/harness/alpha/company_os/`

Durable, multi-tenant autonomous organizations **over the real subsystems**. This
package is an index and governance layer. It owns the organisation; everything it
manages lives in the subsystem that already owns it.

| Concept | Real owner |
| --- | --- |
| Employee | `alpha.bots` profile (`agent_handle`) |
| Project | `alpha.projects` project (`project_id`) — **Gateway-owned repo** |
| Work item | `alpha.kanban` card (`task_id`) |
| Group / meeting room | `alpha.groups` room (`room_id`) |
| Schedule | `alpha.scheduler` / `alpha.automations` (indexed, never fired) |
| Tick ledger | this package, append-only JSONL |

## The three rules that decide every review question

1. **Index, never duplicate.** A second project or board model here would be the
   duplication this package exists to remove. Every id in `PortfolioLink`,
   `WorkItemLink`, `GroupLink`, `ScheduleLink` is another subsystem's id.
2. **Measured or `None`.** `MeasurementBasis` is serialized beside every figure
   that needs provenance. An unmeasured value is `None` with
   `basis="unmeasured"` — never a flattering zero, never an invented percentage.
   `company_health()` returns `health_percent=None` for a company with no work,
   which is the honest answer an earlier implementation got wrong.
3. **A proposal is never an action.** Anything the loop may not do itself becomes
   an `ApprovalRequest`. Approving is a separate, explicit, owner-scoped mutation,
   and a rejection is kept rather than deleted so a later reviewer sees what was
   proposed and why it was declined.

## Tenancy

`owner_id` is **server-assigned** and is the only tenant boundary. The Gateway
resolves it from the authenticated principal (`routers/companies.py::_owner`);
nothing here reads an owner from a request body. An ownerless read of a company
that exists under a different owner returns `None`, which the router turns into
**404** — never a 403 that would confirm another tenant's company exists.

`CompanyStore._owner_ids` scans the store directory as a *rescue* path for a
company written but never indexed, and **every rescued id is ownership-checked by
reading its document**. An unguarded scan would leak another tenant's companies
into this owner's listing.

`CompanyStore.list_owner_ids()` is the single system-wide read. It exists only
for the background autonomy sweep, which has no request context, and must never be
reachable from an HTTP route.

## Durability

`store.py` copies `alpha.routines`' discipline because that module already solved
it correctly:

- **Atomic** — every write is `tmp` + `os.replace`.
- **Versioned** — a document written by a newer `COMPANY_STORE_SCHEMA_VERSION` is
  **refused**, never guessed at. Reinterpreting an unknown shape is how a company
  quietly loses its workforce.
- **Loud when unreadable** — a missing file is a fresh install; a file that exists
  but will not parse raises `CompanyStoreUnreadable` rather than reverting to an
  empty roster, because an empty roster reads exactly like "this owner has no
  companies" and is indistinguishable from data loss.
- **Append-only ledger** — `TickRecord`s go to JSONL and are never rewritten.

**Scope.** This adapter is atomic and restart-recoverable for a single Gateway
process. It is **not** a shared multi-worker lease repository and must never be
described as cross-process exactly-once.

## The loop, and its five breakers

`orchestrator.step()` is **cadenced, not continuous**: a bounded, model-free,
restart-recoverable tick that runs gate → observe → plan → act → verify → ledger →
report. Real companies run a daily standup, a weekly review, a monthly OKR check
and a quarterly board on top of continuous triage; that structure is copied here
because it is how organizations work *and* because an unpaced infinite loop is
how an agent fleet burns a budget while nobody is watching.

Because the tick calls no model, it is predictable, restartable, and safe to run
unattended — and enabling the supervisor loop spends no tokens.

Every gate in `loop_safety.py` is evaluated **before** any work, because a budget
checked after the spend is not a budget:

| Breaker | Prevents |
| --- | --- |
| `BUDGET` | an unbounded loop billing you while you sleep |
| `NO_PROGRESS` | retrying the same failing thing forever |
| `TICK_BUDGET` | one tick fanning out to a hundred agents |
| `FAILURE_STORM` | a systemic regression reading as "lots of activity" |
| `KILL_SWITCH` | running while the operator has stopped the fleet |

`check_budget` **refuses when no daily ceiling is declared** rather than
defaulting to an allowance: an invented ceiling is a ceiling nobody agreed to.
`bounded_actions` returns deferred work instead of dropping it — a dropped action
is a silently lost task, the worst outcome a work queue can have.

An **idle** company is a *healthy* state that costs nothing and is reported as
`cost_usd=0.0, cost_basis="measured"`. A tick that dispatched work reports
`cost_usd=None` because no token meter exists for it; that is the only honest
answer.

## Workforce rules

- Hiring goes through `BotRegistry.hire_bot`, so the existing authority ceiling,
  grant intersection and population ceiling all still apply. The registry's reason
  for a refusal is preserved verbatim.
- `COMPANY_ACTOR = "alpha"` satisfies `SELF_EXTENSION_ACTORS`. It is a **server
  constant, never caller input** — a request body cannot name itself `alpha`.
- `hire_blueprint` is **transactional**: a failed batch retires every profile it
  created, because a company that looks staffed but is missing half its team is
  worse than one that failed. Profiles that already existed are never touched.
- Every bundled blueprint names a **real `alpha.bots` template slug**
  (`ceo`, `architect`, `coder`, `tester`, `security`, `sre`, …). A template that
  does not resolve is dropped rather than passed on, because `get_or_create` would
  otherwise land the hire on a generic placeholder profile.
- Reuse is reported separately from creation (`already_existed`), so "hired 10" is
  never claimed when only 3 profiles were actually created.
- `update_bot` is a plain field writer with **no** actor gate, unlike
  `hire_bot`/`rescope_bot`: org placement is metadata on a profile the company
  already owns, not a new grant of authority. Do not add an `actor=` there.

## Boundaries this package must respect

- **The harness never imports `app.*`** (`tests/test_harness_boundary.py`).
  `get_project_repo` lives in `app.gateway.deps`, so **this package cannot
  enumerate projects**. `portfolio.index_projects()` takes rows the caller read;
  the Gateway router supplies them. That is the correct direction.
- **`alpha.scheduler` owns *when*.** The company indexes which schedules serve it
  and never fires one. Two owners for one occurrence is the failure this
  separation prevents.
- **Announcements go to a room; liveness is a silent pulse.** A heartbeat that
  posts to chat trains everyone to ignore the channel.
- **`RunManager` remains the sole run lifecycle owner.** The loop sequences work
  through real subsystems and never spawns a parallel execution stack.

## Background loop

Registered as `company_operations` in `AutonomySupervisor.register_default_loops()`
with adapter `app.gateway.autonomy.loops:company_operations_tick`. **Default off**
— an absent id in `config.yaml -> autonomy.loops` means disabled.

The loop id is matched by the manifest generator's regex `r'\("([a-z_]+)",\s*"'`,
so it must stay `[a-z_]+` and remain the first element of a tuple followed by a
double-quoted string, or the manifest silently omits it and
`test_supervisor_loops_match_the_manifest` fails.

The sweep skips a company whose own policy is not `enabled` and **reports** a
company whose breaker refuses — "the loop did nothing" and "three companies are
refusing to run" are different operational facts. Each company is isolated, so one
failure cannot park the loop.

## Tests

`backend/tests/test_company_os_core.py` — durability across a restart, tenant
isolation, the evidence-honesty inversions (unmeasured progress/health/spend are
`None`), every breaker, blueprint validity for all five archetypes, and the model
invariants. Frontend: `frontend/src/lib/company.test.mjs` pins the exact routes
and the honesty inversions in the client.