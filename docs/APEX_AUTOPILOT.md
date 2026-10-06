# Operating APEX Autopilot

APEX is Alpha's **executive control plane**. It holds an autonomy contract and
runs a bounded decision cycle over engines that already exist. It is not an
execution engine, and reading it as one is the mistake this page exists to
prevent.

Design specification: [`ALPHA_APEX_AUTOPILOT_MASTER_SPEC.md`](ALPHA_APEX_AUTOPILOT_MASTER_SPEC.md)
(198 sections). Inventory and rationale:
[`APEX_INTEGRATION_MAP.md`](APEX_INTEGRATION_MAP.md). Module guide:
`backend/packages/harness/alpha/apex/README.md`.

---

## 1. What APEX does, in one paragraph

You give it an objective and a profile. It records the objective as a durable
**session**, decides what should happen next on each pass, records that decision
with the reason it chose, and refuses to mark the session complete until an
acceptance report in which every criterion was evaluated and held. A **host
adapter** performs the selected action — APEX never runs a tool, starts a run,
or touches a sandbox.

```
objective → session → cycle { decide, record, checkpoint } → host adapter → acceptance gate → COMPLETED
```

---

## 2. Enabling it

Off by default, like all ten Alpha background loops.

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

Sessions and their event journal live under `runtime_home()/apex/`
(`sessions.json` + `events.jsonl`).

---

## 3. Profiles

| Profile | Authority | Budget shape | Protected actions |
|---|---|---|---|
| `off` | nothing granted | all zeros | every class needs approval |
| `assist` | everything except host/network-reaching authority | 1 agent, 200 tool calls, 60 min | adds `git_commit`, `shell_execution`, `package_install` as **approval** |
| `autonomous` | everything | 6 agents, 2500 calls, 480 min | those three become **allow**; `package_install` stays approval |
| `apex_max` | everything | 12 agents, 5000 calls, 1440 min | those three become allow |

Two things never move with the profile:

- **The emergency stop.** `ApexControls(emergency_stop=False)` raises. There is
  no setter, and the contract carries no field that could hold `False`.
- **The protected block.** `destructive_filesystem`, `credential_changes`,
  `identity_changes`, `external_publication`, `irreversible_external_action`
  are approval at every profile; `secret_export` and `financial_action` are
  denied outright. `POST /api/apex/sessions` rejects a request to loosen one
  with a 422 naming it.

Ascending a profile raises **budget ceilings only** — never authority beyond
the profile's own ceiling, never the stop, never a protected action.

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
| `checkpoint` | seal the cycle |

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

---

## 5. The acceptance gate

`COMPLETED` is reachable by exactly one path: `alpha.mission.acceptance.assert_acceptance_passed`.

| Criterion state | Decision | Session |
|---|---|---|
| none evaluated | `await_verification`, blocked | unchanged |
| evaluated, did not hold | `recover` | `active` |
| evaluated, all hold | `report` | `completed` |

The HTTP state route refuses **every** terminal value with a 409, not just
`COMPLETED` — a request body that could assert `failed` or `cancelled` would be
the same false-completion hole under a different label.

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
| `GET` | `/api/apex/sessions/{id}` | one session |
| `DELETE` | `/api/apex/sessions/{id}` | remove the row |
| `POST` | `/api/apex/sessions/{id}/cycle` | one cycle; admin |
| `POST` | `/api/apex/cycle` | one cycle per non-terminal session; admin |
| `POST` | `/api/apex/sessions/{id}/steer` | record a constraint |
| `POST` | `/api/apex/sessions/{id}/state` | non-terminal transition only |
| `GET` | `/api/apex/sessions/{id}/events` | SSE, journal replay + live tail |
| `GET` | `/api/apex/status` | the §59 projection (read-only) |
| `GET` | `/api/apex/policy?profile=…` | contract + attributed policy sites |
| `GET` | `/api/apex/invariants` | §188 I1–I12 and their live sites |

Collection routes are declared before `/sessions/{id}`; Starlette matches in
registration order, and the reverse order answers
`404 Session 'invariants' not found`. Pinned by
`tests/test_apex_api.py::TestRouteOrder`.

Control endpoints (`POST`) require an administrator. Reads do not.

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

- **APEX decides; it does not execute.** Creating a session and running a cycle
  produce a *decision*. Something else has to perform it. There is no shipped
  host adapter that drives `RunManager`, the DWE or the swarm — the cycle's
  outputs are for a host to consume.
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
- **JSON/JSONL state is process-local.** `sessions.json` and `events.jsonl` are
  atomic and restart-recoverable for one Gateway. They are not a shared
  multi-worker store and not cross-process exactly-once — the same statement
  `runtime/AGENTS.md` makes for swarms, dynamic workflows and the peer network.
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
| session list reports `available: false` | `sessions.json` is unreadable; `reason` carries the exception |
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

Gates that must also stay green: `test_feature_manifest_wiring.py`,
`test_no_orphan_modules.py`, `test_harness_boundary.py`,
`test_autonomy_supervisor.py`, and `scripts/check_generated_drift.py`.

---

## 12. See also

- [`ALPHA_APEX_AUTOPILOT_MASTER_SPEC.md`](ALPHA_APEX_AUTOPILOT_MASTER_SPEC.md) — the 198-section specification
- [`APEX_INTEGRATION_MAP.md`](APEX_INTEGRATION_MAP.md) — what already existed, and what was deliberately not built
- [`PRODUCTION_READINESS_INVENTORY.md`](PRODUCTION_READINESS_INVENTORY.md) — what is implemented, repository-wide
- [`SELF_AWARENESS.md`](SELF_AWARENESS.md) — the self-inventory plane APEX defers to
- [`architecture/durable-runtime.md`](architecture/durable-runtime.md) — the lifecycle owners APEX composes