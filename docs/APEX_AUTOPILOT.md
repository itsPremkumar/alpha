# Operating APEX Autopilot

APEX is Alpha's **executive control plane**. It holds an autonomy contract and
runs a bounded decision cycle over engines that already exist. The Gateway host
adapter starts and observes selected runs through the shared `RunManager`; the
APEX decision cycle itself does not execute tools or touch a sandbox.

Design specification: [`ALPHA_APEX_AUTOPILOT_MASTER_SPEC.md`](ALPHA_APEX_AUTOPILOT_MASTER_SPEC.md)
(198 sections). Inventory and rationale:
[`APEX_INTEGRATION_MAP.md`](APEX_INTEGRATION_MAP.md). Module guide:
`backend/packages/harness/alpha/apex/README.md`.

---

## 1. What APEX does, in one paragraph

You give it an objective and a profile. It records the objective as a durable
**session**, decides what should happen next on each pass, records that decision
with the reason it chose, and refuses to mark the session complete until an
acceptance report in which every criterion was evaluated and held. The Gateway
host adapter dispatches the selected objective through `RunManager`; the run
uses Alpha's normal tool, sandbox, governance, and subagent boundaries.

```
objective → session → cycle { decide, record, checkpoint } → host adapter → acceptance gate → COMPLETED
```

---

## 2. Enabling it

Off by default, like all ten Alpha background loops.

**The switch.** Three ways to turn it on, all the same code path:

- **The UI** — the APEX panel's ON/OFF toggle (the `apex` workspace view).
- **Chat** — `/apex on [assist|autonomous|apex_max]`, `/apex off`.
- **HTTP** — `POST /api/apex/enable`, `POST /api/apex/disable` (admin).

The toggle is per conversation, persists for that session, and defaults to
the `assist` profile. `/apex` on its own reports status; it never enables.
See [§7](#7-api) for the mode routes and [the command table](#the-apex-commands).

It can also be enabled for the background loop in `config.yaml`:

```yaml
# config.yaml
autonomy:
  loops:
    apex:
      enabled: true
      interval_seconds: 120
      jitter_seconds: 15
      max_concurrent: 1
```

Then create a session:

```bash
curl -X POST localhost:8001/api/apex/sessions \
  -H 'content-type: application/json' \
  -d '{
        "objective": "fix the failing coding workflow",
        "profile": "autonomous",
        "acceptance_criteria": ["backend suite passes", "no regression in web tests"]
      }'
```

The background `apex` loop also requires that session's own scope to be ON;
enabling the loop in `config.yaml` only schedules bounded checks. Unknown or
unreadable mode state never grants work.

Sessions and their event journal live under `runtime_home()/apex/`
(`sessions.json` + `events.jsonl`).

---

## 3. Profiles

| Profile | Authority | Budget shape | Protected actions |
|---|---|---|---|
| `off` | nothing granted | all zeros | every class needs approval |
| `assist` | everything except host/network-reaching authority | unlimited spend; 1 agent, depth 1, 2 replans, 1 retry per failure class | adds `git_commit`, `shell_execution`, `package_install` as **approval** |
| `autonomous` | everything | unlimited spend; 6 agents, depth 3, 10 replans, 3 retries per failure class | those three become **allow**; `package_install` stays approval |
| `apex_max` | everything | unlimited spend; 12 agents, depth 5, 20 replans, 4 retries per failure class | those three become allow |

Two things never move with the profile:

- **The emergency stop.** `ApexControls(emergency_stop=False)` raises. There is
  no setter, and the contract carries no field that could hold `False`.
- **The protected block.** `destructive_filesystem`, `credential_changes`,
  `identity_changes`, `external_publication`, `irreversible_external_action`
  are approval at every profile; `secret_export` and `financial_action` are
  denied outright. `POST /api/apex/sessions` rejects a request to loosen one
  with a 422 naming it.

Ascending a profile raises operational capacity only — never authority beyond
the profile's own ceiling, never the stop, never a protected action. Every
enabled profile has unlimited per-session tool-call, token, and elapsed-runtime
quotas; usage budgets never stop goal work. Agent concurrency, delegation depth,
retries, and replans remain bounded operational controls.

### Narrowing

A mission may hold *less* than its profile offers, never more:

```bash
-d '{"objective":"...","profile":"autonomous",
    "authority":{"terminal":false,"browser":false},
    "budget":{"max_tool_calls":200}}'
```

Widening raises `ContractViolation` → HTTP 422 with the field named.

---

## 4. The cycle

One pass runs four steps and returns all of them, which is what makes spec §177
("why was this chosen") answerable after the fact:

| Step | What it does |
|---|---|
| `load_session` | resolve the row; **absent** blocks rather than guessing |
| `check_policy` | compare the session's contract digest with the live one |
| `apply_decision` | record the decision; transition state only where the decision implies it |
| `checkpoint` | re-read the row and increment `cycle_count`, reporting **absent** (never a count) if the row is gone, and **skipped** when the decision was `none` — a parked session's pass writes nothing |

The background cycle and dispatcher scan sessions in stable, bounded pages so
older sessions remain reachable as the session count grows. A request to
dispatch one session resolves that id directly rather than searching only a
recent-session window.

The `cycle.decision_recorded` event means the executive selected and journaled
an action; it does not mean a mission or run was dispatched. The Gateway host
adapter reports run admission separately as `run.dispatched`, which carries the
RunManager id and dispatch generation. The executive event is intentionally not
named `cycle.dispatched`.

Before choosing an action, `check_policy` compares the session's stored
contract digest with the active contract. A mismatch returns a blocked
`policy_drift` decision and skips planning or dispatch. This session stays
blocked; create a fresh session under the intended contract after reviewing its
recorded objective and current state. Since enabled profile defaults changed
from finite spending quotas to unlimited, sessions created by an older release
may encounter this check and need a fresh session to adopt the new defaults;
their frozen contracts are never silently widened.

If the policy check itself cannot read or validate the stored digest, APEX also
parks the session as `policy_drift`; it does not continue under the active
contract. That park has no approval record because approval cannot repair a
frozen contract. Failures reading other decision inputs are reported in the
cycle steps and park the session with an operator approval request.

```bash
curl -X POST localhost:8001/api/apex/sessions/<id>/cycle
```

```json
{
  "decision": {"action": "await_verification", "reason": "acceptance_pending",
               "confidence": 1.0, "blocked": true,
               "detail": {"refusal": "acceptance criteria were not all evaluated: 2 of 2 unevaluated"}},
  "steps": [{"name": "load_session", "outcome": "loaded", "detail": "fix the failing..."}, ...]
}
```

The cycle is **model-free**. That is deliberate: a deterministic substrate is
what makes the decision reproducible, and model-driven planning enters through
the adapters a host supplies rather than from inside the loop.

APEX-bound runs now carry a Gateway-stamped session id into the shared runtime
tool middleware. Ordinary and durable-batch subagents inherit that trusted
marker, so their own child tool calls enter the same APEX gate after dispatch
and after worker recovery. Before each tool executes, that gate reloads the persisted
session and mode, checks owner, active state, contract digest, authority,
runtime ceiling, and an atomic durable tool-call budget reservation. Tools that
need approval park the session; an approval is bound to the tool name, action
class, contract digest, and a digest of its exact arguments, and can be consumed
once. Ordinary runs without the server-stamped APEX id keep their existing
policy path. The session store is process-local JSON, so this budget is not a
cross-process exactly-once guarantee.

The Gateway host adapter admits one objective run through the existing
`start_run()` service and `RunManager`. `POST /api/apex/sessions/{id}/dispatch`
starts or observes it immediately; the configured `apex` supervisor loop does
the same automatically. Each dispatch has a durable generation and a stable
idempotency key, so a restart between run admission and session-link recording
reuses the same RunManager record. The session exposes `run_id`, `run_status`,
`dispatch_state`, and measured input/output tokens and LLM-call totals. All
enabled profiles set token, tool-call, and elapsed-runtime quotas to `null`,
meaning unlimited: usage spending ceilings will not stop the mission. Ordinary
`task` concurrency is capped by the session's finite active-agent and parallel
task fields. Acceptance-failure recoveries now increment a durable session
counter and park the session when its frozen replan ceiling is reached; that
ceiling cannot be widened through approval, so a new session is required to
continue. Safe APEX run recovery durably reserves each retry by failed run id and
failure class against the session's frozen retry ceiling; the runtime's global
resume-attempt ceiling remains an outer bound. This reservation shares the
session store's single-process coordination limit. The Gateway `task` path does
not apply the APEX depth value; the subagent lifecycle manager's own depth limit
still applies. Newly created
sessions receive unlimited spending defaults. Existing sessions retain their
frozen contracts and are not silently widened; older sessions whose contract
digest no longer matches the active profile must be reviewed and recreated to
use the new defaults. Alpha still records usage, and engine admission, provider
availability, platform capacity, governance, approvals, and the emergency stop
remain in force; a session quota never reserves or creates hardware or provider
capacity.

When a RunManager run completes, the Gateway adapter evaluates registered
`AcceptanceRegistry` probes off the event loop. Only a fully evaluated report
is submitted to the executive gate; a pass can complete the session and a
measured failure can trigger bounded recovery. If no probe is registered or a
probe cannot decide, APEX remains awaiting owner evidence. Probes must return a
boolean or `None`; any other result stays unverified. Alpha ships no default
probe that can verify arbitrary natural-language criteria. The Gateway host
adapter reads durable `llm.ai.response` and `subagent.end` events
with a persisted per-run sequence cursor, so a repeated supervisor tick or
restart does not count an event twice. If the event store is unavailable or has
not emitted token usage, it upserts cumulative RunManager snapshots for live
usage instead. A usage-less event advances the cursor without claiming zero
tokens; if a later row makes event accounting incomplete, its event totals are
reconciled to a cumulative snapshot. The two sources are never added together.
These values are observations from the linked run, not
estimates. At each supervisor tick the adapter checks a persisted finite runtime
quota, when one was explicitly configured, and asks RunManager to interrupt an
over-budget run; the tick interval determines how late that check can be.

For ordinary `task` calls, `SubagentLimitMiddleware` applies the smaller of
process capacity, `max_parallel_tasks`, and `max_active_agents` to delegated
children. It re-reads and validates the owner, mode, active state, and frozen
contract before each model turn; invalid APEX context allows no child calls.
APEX `batch_task` submissions persist that session's frozen concurrency ceiling.
Lease admission serializes by session in the batch database and counts active
leases across that session's active durable batches, including across Gateway
workers. The ceiling does not yet combine ordinary `task` children with durable
batch workers into one shared count; their process admission limits still apply.
`SafeRunRecoveryService` may continue a failed APEX run only when the current
checkpoint proves the pending node is safe to resume, the source run is still
the session's linked run, and owner, thread, active state, and dispatch
generation still match. It compare-and-set links the newly admitted RunManager
record back to the same APEX session; if the process stops in that gap, startup
reconciles the newest run only when its server-stamped recovery lineage and
APEX generation still match.
Recovery remains bounded by the runtime retry policy. A stale binding is
stopped; an unsafe or ambiguous pending side effect remains parked for operator
review rather than being replayed.

A run ending is not objective completion. A completed run enters
`awaiting_verification`; failed or interrupted work enters `failed` and is not
silently re-dispatched. The session owner can submit one measured result with
evidence for every declared criterion through
`POST /api/apex/sessions/{id}/acceptance`. Alpha does not infer criterion
results from the model's summary and does not yet ship automatic test, artifact,
or HTTP evidence collectors; the submitted evidence must identify the actual
check or artifact. A report that passes closes through the executive acceptance
gate. A complete report with a failed criterion is journaled and selects a new
dispatch generation for recovery. The usage projection still lacks estimated
cost and per-session CPU/RAM measurements; those remain in separate runtime
surfaces or unmeasured.

When a finite token ceiling is configured, the Gateway supervisor tick and tool
gate check it using the latest measured usage. A running model request cannot
be preempted mid-response, and tick scheduling means the final measured total
can exceed the ceiling by one in-flight call. A zero token ceiling prevents
dispatch; `null` means no APEX per-session ceiling. These are token limits, not
currency limits: provider pricing may be unknown, and APEX still does not enforce
per-session CPU/RAM ceilings. Inspect
the run usage and host process telemetry for those dimensions.

---

## 5. The acceptance gate

`COMPLETED` is reachable by exactly one path: `alpha.mission.acceptance.assert_acceptance_passed`.

| Criterion state | Decision | Session |
|---|---|---|
| criteria declared, no report | continue to `create_mission` / `plan` / `dispatch` | unchanged |
| report exists, criteria unevaluated | `await_verification`, blocked | unchanged |
| evaluated, did not hold | `recover` | `active` |
| evaluated, all hold | `report` | `completed` |

The owner submits a complete result set after the linked RunManager run ends:

```http
POST /api/apex/sessions/{id}/acceptance
Content-Type: application/json
```

```json
{"results":[{"criterion":"focused checks pass","met":true,"evidence":"pytest output: 12 passed"}]}
```

Declaring criteria at session creation cannot itself ask for verification
before any work has run. The report must cover every declared criterion
exactly once; missing, duplicate, or undeclared results are rejected. A
blocked session still requires its pending approval before this control can
move it. Reports are stored with the session and journaled so a Gateway
restart does not erase the evidence or failed-run recovery decision.

The HTTP state route refuses **every** terminal value with a 409, not just
`COMPLETED` — a request body that could assert `failed` or `cancelled` would be
the same false-completion hole under a different label.

### The approval gate

A cycle that cannot proceed does not loop and does not fail silently. It
**parks** the session and asks:

1. The decision comes back `blocked` (typically `acceptance_pending`).
2. The executive moves the session to `BLOCKED` and creates **one pending
   `ApprovalRecord`** naming the blocker as its `note`.
3. While parked, `select_next_action` answers `NONE` with
   `awaiting_operator_approval` — autonomy cannot un-park itself.
4. Only an operator verdict moves it: `approve` sets the session back to
   `ACTIVE`; `reject` keeps it parked with the refusal on record. A second
   verdict on the same approval is refused (409), never an overwrite.

**The control verbs refuse a parked session.** `/apex pause`, `/apex resume`,
`/apex stop` and `/apex take-over` — and their HTTP twins — return an error
naming the approval route instead of moving `BLOCKED`. Without that rule,
pause-then-resume would un-park the session in two commands: the gate with a
side door. The refusal also says the park already stops the work, so an
operator arriving with stop intent is told the session decides nothing rather
than being sent to un-park it first. `steer` still works while parked — a
constraint is a record, not a control — and a rejected park is moved only by
   `replan`. Policy drift is the exception: it parks without an approval
   because no verdict can make the frozen contract match; review and create a
   new session instead.

Pending verdicts are listed at `GET /api/apex/approvals` (and rendered by the
APEX panel's session-control card, which re-reads after every action).

---

## 6. Fleet control

The cycle reads `alpha.runtime.control` and `alpha.runtime.estop` — the same two
sources `AutonomySupervisor` gates its loops through. Nothing new was invented,
so one operator action stops both the loop and the cycle.

| Fleet state | Cycle result |
|---|---|
| `RUN`, no sentinel | decides normally |
| `PAUSE` | blocked, `source: fleet_control:pause` |
| `ESTOP` | blocked, `source: estop_sentinel` |
| control file unreadable | **blocked**, `source: control_unreadable:<exc>` |

The last row is load-bearing: a stop mechanism that cannot read its state must
stop.

---

## 7. API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/apex/sessions` | create under a profile; admin |
| `GET` | `/api/apex/sessions` | list; degraded store reports `count: null` |
| `GET` | `/api/apex/sessions/{id}` | one session; owner-scoped (a foreign id is 404) |
| `DELETE` | `/api/apex/sessions/{id}` | remove the row; admin |
| `POST` | `/api/apex/sessions/{id}/cycle` | one cycle; admin |
| `POST` | `/api/apex/sessions/{id}/dispatch` | start or observe the session's idempotent RunManager run; admin |
| `POST` | `/api/apex/sessions/{id}/acceptance` | submit complete measured evidence after a successful run; session owner |
| `POST` | `/api/apex/cycle` | one cycle per non-terminal session; admin |
| `POST` | `/api/apex/sessions/{id}/steer` | record a constraint; owner-scoped (narrowing is not an admin act) |
| `POST` | `/api/apex/sessions/{id}/state` | non-terminal transition only; admin |
| `GET` | `/api/apex/sessions/{id}/events` | SSE, journal replay + live tail; owner-scoped |
| `GET` | `/api/apex/status` | the §59 projection (read-only); its `contract` block is the one in force for `scope_key` (defaulting like `/mode`), and a `session_id` is owner-scoped (a foreign id is 404) |
| `GET` | `/api/apex/policy?profile=…` | contract + attributed policy sites |
| `GET` | `/api/apex/invariants` | §188 I1–I12 and their live sites |
| `GET` | `/api/apex/mode` | the ON/OFF state for a scope, plus its `active_session` |
| `POST` | `/api/apex/enable` | turn APEX on; admin |
| `POST` | `/api/apex/disable` | turn APEX off; admin |
| `POST` | `/api/apex/pause` | park the scope's active session; admin |
| `POST` | `/api/apex/resume` | release a paused session; admin |
| `POST` | `/api/apex/stop` | mission-scoped park carrying the RunManager/ESTOP note; admin |
| `GET` | `/api/apex/goals` | list; owner-scoped; degraded store reports `count: null` |
| `POST` | `/api/apex/goals` | create a goal or subgoal; authenticated, the owner is the caller |
| `GET` | `/api/apex/goals/{id}` | one goal and its subtree; owner-scoped |
| `POST` | `/api/apex/goals/{id}/steer` | record a constraint on the goal; owner-scoped |
| `POST` | `/api/apex/goals/{id}/replan` | move a goal back to replanning; owner-scoped |
| `POST` | `/api/apex/goals/{id}/verify` | verify against the success criteria; owner-scoped |
| `GET` | `/api/apex/goals/{id}/tasks` | the decomposition; owner-scoped |
| `GET` | `/api/apex/goals/{id}/agents` | specialists *recorded as asks* (this route never spawns); owner-scoped |
| `GET` | `/api/apex/goals/{id}/workflow` | reports `available: false` — not implemented, by design |
| `GET` | `/api/apex/goals/{id}/events` | the goal journal; owner-scoped |
| `GET` | `/api/apex/goals/{id}/decisions` | the goal's session's cycle decisions; owner-scoped |
| `GET` | `/api/apex/goals/{id}/evidence` | measured evidence; owner-scoped |
| `GET` | `/api/apex/goals/{id}/failures` | failures derived from the record; owner-scoped |
| `GET` | `/api/apex/approvals` | pending verdicts; bounded list with `returned`/`truncated`; degraded store reports `count: null` |
| `POST` | `/api/apex/approvals/{id}/approve` | approve and un-park; admin |
| `POST` | `/api/apex/approvals/{id}/reject` | reject — the park stands; admin |

Collection routes are declared before `/sessions/{id}`; Starlette matches in
registration order, and the reverse order answers
`404 Session 'invariants' not found`. The three `/mode`, `/enable` and
`/disable` routes are declared before it for the same reason — a catch-all
first would answer `405 Session 'enable' not found` for a feature that
exists. Pinned by `tests/test_apex_api.py::TestRouteOrder` and
`tests/test_apex_mode.py`.

**Who may call what**, in one place, because every route now resolves its
principal through the same two helpers:

- **Administrator** — `create`, `delete`, `set_state`, `run one cycle`, the
  mode toggle, and the control/approval verdicts. Admin is decided by
  `app.gateway.deps.is_admin_user` (the shared predicate, which also refuses a
  PAT as the administrator), never by a local `getattr(user, "is_admin")`: no
  principal in this build carries that field, so a private copy would answer
  `403` for a real administrator while a test fixture faked the attribute.
- **Owner-scoped** — every member route (`GET /sessions/{id}`, the SSE stream,
  `steer`, `state`, `delete`, and all ten goal subresources) is resolved through
  `_session_or_404` / `_goal_or_404`, so a session or goal that belongs to
  someone else answers **`404` with the same detail as an absent row**. A `403`
  would confirm the id exists; the collection routes are already owner-scoped,
  so the member route has to close the same hole. An administrator bypasses
  owner scoping. `steer` stays owner-scoped but *not* admin-gated — narrowing a
  session is always allowed to whoever owns it.
- **Unauthenticated** — `401`, and the answer is `401` for *every* id: with no
  principal there is no owner to match against, so the route must not fall back
  to "no owner filter" and hand back all rows.
- **Degraded store** — a member read of an unreadable store answers **`503`
  with the reason**, not `404`: "we could not look" and "there is nothing
  there" lead to opposite actions. A genuinely absent id still answers `404`.
- **`GET /approvals` is bounded.** It returns at most `limit` rows (default and
  maximum 200) while `count` and `pending` keep describing the *whole* matching
  set, so the response carries `returned` and `truncated` for the UI to
  disclose. An unbounded backlog would make the endpoint a slow read that
  grows with every decided verdict.

### The mode toggle

`GET /api/apex/mode` is the read the UI switch renders from. Two fields in
it are deliberately not one:

- **`enabled`** — the recorded intent. Did anything switch this scope on?
- **`contract_enabled`** — what the frozen contract actually grants, and so
  what gates work.

They disagree in the cases that matter: an unreadable mode store answers
`enabled: false` for every scope (fail-closed), and a record persisted by a
newer build under a profile this one does not know degrades to `assist` — so
`enabled` can be true while the authority is not what was asked for. The
response also carries `durable` (whether the write reached disk), `load_error`
(a degraded read, never a clean OFF) and `load_note` (an unrecognised stored
profile).

State lives at `runtime_home()/apex/mode.json`, one row per scope, plus an
append-only `mode_events.jsonl` journal recording who enabled what, at which
profile, and when. A scope with no row reads as OFF — an unknown session is
not enabled.

The read also carries **`active_session`** — the session bound to this scope
(or `null` when none exists) — because the control verbs and the approval gate
act on exactly that session, and because "APEX is off" and "APEX is off and
no session was ever created" are different facts to render.

`POST /api/apex/disable` retains the profile, so a later enable restores the
authority the operator had rather than resetting them to a default. Both
writes are idempotent and report `changed: false` on a repeat.
`durable` reports an actual write outcome; reads and idempotent no-op writes
return `null`, because neither performed a persistence attempt.

The picker on that switch offers **only the profiles an enable may carry** —
`assist`, `autonomous`, `apex_max` — and adopts the server's profile only when
it is one of those. `off` is a state, not a rung: a scope nobody has enabled
yet reads as `off`, and adopting it as the picker's value left the displayed
rung and the submitted one disagreeing, so the first-ever "Turn on" posted
`{"profile":"off"}` and was refused. The refusal itself is correct and stays
server-side; the panel simply never produces a body the server names as invalid.

The same switch is also **in the composer**, beside the reasoning picker
(`ApexModePicker`), so it is reachable from every chat surface without opening
the APEX view — `Composer` is mounted once in `ChatView`, so bots, groups and
DMs share one control. It reads and writes the **same default scope**, not a
per-conversation one: a second, thread-scoped switch would be a second "APEX is
on" claim beside the panel's, and the two surfaces would be free to disagree.
Its menu lists every profile with what the contract actually grants at it, and
states inside the menu what the control does **not** do — it sets the autonomy
contract for the scope but does not itself create or dispatch an objective. The
APEX panel's explicit **Create and dispatch** form uses that enabled server
profile and scope to create a session and start its run through the Gateway
adapter. A read that failed renders `unknown` with the
server's reason and offers no rung at all; a write that was refused changes
nothing and keeps its reason on screen.

### The `/apex` commands

The same switch, reachable from chat, from `POST /api/commands/execute`, and
from the model-facing `execute_slash_command` tool — one implementation behind
all three, because two independently written toggles would eventually disagree
about what "on" means.

Fourteen verbs: the five the switch needs, plus the nine that move or inspect
one conversation's session.

| Command | Effect |
|---|---|
| `/apex` | status — on/off, profile, contract, invariants, fleet control |
| `/apex on [assist\|autonomous\|apex_max]` | enable for this conversation |
| `/apex off` | disable, preserving mission state |
| `/apex status` | same as `/apex` |
| `/apex policy [profile]` | what a profile grants, budgets, and refuses |
| `/apex pause` | park this conversation's session; the executive decides nothing further |
| `/apex resume` | release a paused session |
| `/apex stop` | park this mission's APEX work; in-flight runs belong to `RunManager` |
| `/apex steer <instruction>` | record a mission constraint — never a prompt rewrite |
| `/apex take-over` | park APEX and record, at high priority, that the operator drives |
| `/apex approve` | approve the pending approval and resume the parked session |
| `/apex reject` | reject the pending approval; the session stays parked |
| `/apex replan` | move this session's non-terminal goals back to replanning |
| `/apex verify` | verify this session's goals against their success criteria |

Properties worth knowing:

- **`/apex` alone never enables.** A bare or truncated line routes to status.
  The one control that grants autonomy is the explicit `on`.
- **The default profile is `assist`, not `apex_max`.** Turning APEX on is not
  a request for maximum authority; raising it is a separate, explicit act.
- **Scoping follows the conversation.** The handler resolves the session,
  thread or conversation id from the dispatch context, so two conversations
  never share a toggle, and `/apex on` in one never reaches another. The
  session join prefers an active session for the scope and falls back to the
  latest one, so a completed mission answers "that session is terminal"
  instead of pretending no session ever existed.
- **A parked session is the approval gate's to move.** `pause`, `resume`,
  `stop` and `take-over` refuse on `BLOCKED` with the pending approval named —
  see [the approval gate](#the-approval-gate).

A bad profile is refused with the valid ones named, so a typo cannot silently
grant a different authority than the operator asked for.

---

## 8. Two status fields worth knowing

### `contract.policy_sites_missing`

APEX delegates every policy decision. This field lists the delegated kernels
that are **absent** — so an operator sees an unenforced boundary instead of
inferring coverage from APEX's presence.

```bash
curl -s localhost:8001/api/apex/policy | jq .policy_sites_missing
```

### `/api/apex/invariants` — `declared` vs `live`

The spec's twelve invariants are each enforced by a named module elsewhere.
`live` reflects whether that module imports **and** exposes the named symbol;
a site that cannot be imported reports `live: false` with the reason. An
invariant row whose enforcement site is missing is reported, never scored as a
pass, and the aggregate never collapses "12 declared" into one number.

```bash
curl -s localhost:8001/api/apex/invariants | jq '{declared, live, all_live}'
```

---

## 9. Honest boundaries

Read these before treating a green status as a working system.

- **The executive decides; the Gateway adapter dispatches.** Creating a session
  and running a cycle produce a recorded decision. The production adapter starts
  one objective run through `RunManager`; automatic DWE or swarm scheduling is
  not part of this adapter.
- **Delegation caps are not one shared cross-path count yet.** The ordinary
  `task` tool is limited by the persisted APEX contract at each model turn.
  Durable APEX `batch_task` leases are aggregated across batches and workers
  under the persisted session cap, but ordinary task children are not included
  in that database-backed batch count.
- **`health` is `unverified` everywhere.** Nothing in this plane probes what it
  lists. This is the self-inventory rule, applied unchanged.
- **Usage is measured or `None`.** `UsageLedger.tool_calls` is `None` until
  something counts a call. It is never `0` by default, because "nothing was
  spent" and "nothing was counted" lead to opposite decisions.
- **Budgets are ceilings, not predictions.** Nothing here forecasts cost.
- **A missing delegated kernel degrades one field, not the projection.**
  `contract_status`, `fleet_status`, `store_status` and the supervisor block each
  report `available: false` with their own reason, so one broken subsystem never
  renders the whole status as healthy.
- **`/api/apex/status` projects the contract in force, not the shipped one.**
  It resolves `scope_key` the way `/mode` does and reads `contract_for(scope)`,
  so a disabled scope answers the OFF contract and an enabled one answers the
  scope's live profile — either way the digest equals `/mode`'s. It used to be
  handed `default_contract()` unconditionally, which printed "Active profile:
  off" beneath an enabled switch *and* made `session_summary` measure every
  session against a contract it was never created under (`contract_drift: true`
  for all of them). Its `session_id` is owner-scoped: a foreign id is 404, not a
  partial read of someone else's objective.
- **The composer's APEX chip changes the contract, not what happens next.**
  Picking a profile in the chat window posts the same `/apex/enable` /
  `/apex/disable` the panel does and then re-reads; it creates no session, sends
  no message and starts no run. Reading it as "this chat is now autonomous" is
  the failure the in-menu disclosure exists to stop.
- **JSON/JSONL state is process-local.** `sessions.json` and `events.jsonl` are
  atomic and restart-recoverable for one Gateway. They are not a shared
  multi-worker store and not cross-process exactly-once — the same statement
  `runtime/AGENTS.md` makes for swarms, dynamic workflows and the peer network.
- **A dropped SSE event is disclosed, never silently eaten.** A live subscriber
  whose queue fills gets an `event: gap` frame carrying how many events were
  dropped and how to recover them (re-read the journal from `after_seq`). A
  `pass` over `asyncio.QueueFull` would leave a stream that looks complete
  while missing events — the failure `MissionEventFeed._overflowed` still
  demonstrates, because that counter is declared and never read.
- **No automatic agent generation, plugin synthesis, or self-modification.**
  Spec §15/§72/§70 are deliberately out of scope; see the integration map.

---

## 10. Troubleshooting

| Symptom | Check |
|---|---|
| loop never runs | `config.yaml -> autonomy.loops.apex.enabled`; absent id = disabled |
| cycles all blocked with `fleet_control:*` | `alpha.runtime.control` state; `GET /api/ops/runtime` |
| cycle stuck at `await_verification` | register an `AcceptanceRegistry` probe, or set a passing report |
| `422 contract refused` | the request tried to widen; narrow instead |
| `409` on `/state` | terminal values are the gate's to assert, not the body's |
| `409` on `/pause`, `/resume`, `/stop` | the session is `BLOCKED` behind the approval gate; decide it with `/api/apex/approvals/{id}/approve` (or reject/replan) |
| command errors with "parked awaiting approval" | same gate — the park already stops the work; the refusal names the verdict routes |
| `403` on a control route | you are not an administrator; the predicate is `is_admin_user`, so a personal-access token never satisfies it |
| `404` on a session you can see in the list | it belongs to another owner — the member routes answer `404` for a foreign id, deliberately, so the id cannot be probed |
| `401` on any `/apex` read | no principal was resolved; with no principal there is no owner to match against, so the route answers `401` rather than falling back to "every owner's rows" |
| `503` on a member read | the store is unreadable; `reason` carries the exception — this is not a missing row |
| `event: gap` on the stream | this subscriber's queue overflowed and events were dropped; the frame says how many and the recovery is a journal re-read from `after_seq` |
| session list reports `available: false` | `sessions.json` is unreadable; `reason` carries the exception |
| approvals list reports `available: false` | `approvals` could not be read; `count` is `null`, not `0` — pending verdicts are unknown |
| an invariant shows `live: false` | `module`/`symbol` name the missing enforcement site |

Logs: `alpha.apex.*` at INFO for refusals and WARN/ERROR for a degraded store
or an unreadable journal.

---

## 11. Tests

| Suite | Covers |
|---|---|
| `tests/test_apex_contract.py` | profiles, budgets, fail-closed, narrowing, attribution |
| `tests/test_apex_executive.py` | decide-only, acceptance gate, fleet control, invariants, status |
| `tests/test_apex_api.py` | route order, refusals, SSE, the supervisor loop |
| `tests/test_apex_mode.py` | the ON/OFF switch: fail-closed, the fourteen `/apex` verbs, the mode routes |
| `tests/test_apex_control.py` | the approval gate and its side-door refusals, the scope joins, the goal operating system, the control/approval/goal routes |
| `tests/test_apex_authz.py` | the admin predicate (`is_admin_user`, PAT refused), owner scoping on every member route, `401`/`403`/`404`/`503` disambiguation, the approvals bound, and the SSE `gap` disclosure |

Gates that must also stay green: `test_feature_manifest_wiring.py`,
`test_no_orphan_modules.py`, `test_harness_boundary.py`,
`test_autonomy_supervisor.py`, `test_discovery_plane_parity.py`,
`test_command_honesty.py`, and `scripts/check_generated_drift.py`.

Frontend: `frontend/src/lib/apex.test.mjs` (routes, verbs, the null-preserving
counters, the control/approval/goal mappings, and the toggle's honesty
inversions).

---

## 12. See also

- [`ALPHA_APEX_AUTOPILOT_MASTER_SPEC.md`](ALPHA_APEX_AUTOPILOT_MASTER_SPEC.md) — the 198-section specification
- [`APEX_INTEGRATION_MAP.md`](APEX_INTEGRATION_MAP.md) — what already existed, and what was deliberately not built
- [`PRODUCTION_READINESS_INVENTORY.md`](PRODUCTION_READINESS_INVENTORY.md) — what is implemented, repository-wide
- [`SELF_AWARENESS.md`](SELF_AWARENESS.md) — the self-inventory plane APEX defers to
- [`architecture/durable-runtime.md`](architecture/durable-runtime.md) — the lifecycle owners APEX composes
