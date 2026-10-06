"""APEX — the executive control plane.

Specification: `docs/ALPHA_APEX_AUTOPILOT_MASTER_SPEC.md` (198 sections).
Integration rationale: `docs/APEX_INTEGRATION_MAP.md`.
Operations: `docs/APEX_AUTOPILOT.md`.
Tests: `tests/test_apex_*.py`; frontend `src/lib/apex.test.mjs`.

## What APEX is

One user-controlled autonomy mode. The user provides an objective; APEX holds an
autonomy **contract** and runs a bounded **executive cycle** that decides what
should happen next. Existing Alpha runtimes perform the work; verification
decides whether it is finished.

```text
user objective
      │
      ▼
AutonomyContract ─── what may happen, up to how much, what always needs a human
      │
      ▼
executive.run_cycle ─── ONE bounded decision pass, recorded with its reason
      │
      ├── host adapter ── performs the selected action (RunManager, DWE, swarm…)
      └── acceptance gate ── the only path to COMPLETED
```

## What APEX is not

The single most important property of this package is what it does **not**
own. Alpha already had a run lifecycle, a background-loop owner, a policy
kernel split across five engines, a workflow engine, a swarm, verifiers, leases
and a durable store. Adding a second of any of those would be a regression, so
this package contains none of them.

`AutonomyContract.policy_sites` records the delegation in machine-readable
form, and `alpha.apex.invariants` names the module enforcing each of the spec's
twelve invariants — so both claims are checkable rather than asserted:

```bash
curl -s localhost:8001/api/apex/policy | jq .policy_sites_missing   # delegated kernels that are absent
curl -s localhost:8001/api/apex/invariants | jq '{declared,live}'   # 12 declared, N live
```

## The three rules

1. **Compose, never duplicate.** Every module either narrows an existing
   decision or aggregates existing state.
2. **Deterministic gates beat model output.** The cycle is model-free. A model
   may propose a plan; code decides whether it is allowed and whether it passed.
3. **No false completion.** `COMPLETED` is reachable only through
   `alpha.mission.acceptance.assert_acceptance_passed`. The HTTP route refuses
   it outright with a 409, because a route that could write that state directly
   would reopen the hole §46 exists to close.

## Layout

| Module | Spec | Purpose |
|---|---|---|
| `contract.py` | §5–§7 | The frozen `AutonomyContract`; narrows, never widens |
| `invariants.py` | §188 | I1–I12 mapped to their live enforcement sites |
| `store.py` | §10, §51, §57, §120 | Durable sessions, usage ledger, steering constraints |
| `executive.py` | §63–§65 | The next-action cycle |
| `status.py` | §55, §59 | One bounded, read-only projection |
| `mode.py` | §3 | The persisted ON/OFF switch a UI toggle actually flips |
| `commands.py` | §3 | The 14-verb `/apex` family, on the shared command registry |
| `goals.py` | §7, §8 | The Goal Operating System: objective, criteria, evidence |
| `strategy.py` | §12, §13, §36, §37 | Strategy choice, swarm sizing, stuck detection |
| `agents.py` | §10, §11, §16, §17, §28 | The specialist factory over existing owners |

## Enabling

Off by default, like every Alpha control plane.

```yaml
# config.yaml
autonomy:
  loops:
    apex:
      enabled: true
      interval_seconds: 120
```

```bash
curl -X POST localhost:8001/api/apex/sessions \
  -H 'content-type: application/json' \
  -d '{"objective":"fix the failing coding workflow","profile":"autonomous",
       "acceptance_criteria":["backend suite passes"]}'
```

Profiles are `off` / `assist` / `autonomous` / `apex_max`. Ascending one raises
budget ceilings only — never the emergency stop, never the protected actions,
and never the authority ceiling, which belongs to
`alpha.bots.authority_ceiling`.