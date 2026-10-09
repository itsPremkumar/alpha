# Governed autonomous variation engine (`alpha/avo/`)

`alpha.avo` already decided *which* variation to try and *whether it looked good
afterwards*. The governance layer is the server-owned wrapper around that decision
machinery which answers the question no part of it could: **whether a proposed
variation may run at all.** One rule holds the whole design together:

> **The model proposes, the server disposes.**

| Module | Owns |
| --- | --- |
| `contracts.py` | the boundary types (`AvoRunRequest`, `ProposedAction`, `EvaluationResult`, `TaskFeatures`, `AvoRunResult`); a digest over every field |
| `policy.py` | the one decision point: risk computed, approval required, budget reserved inside the gate |
| `router.py` | deterministic fast path vs governed-run admission, every unmet condition recorded as a refusal reason |
| `lifecycle.py` | the closed transition graph |
| `budgets.py` | ceiling-owned reserve / charge / release / reconcile |
| `profiles.py` | default-deny capability, evaluator and approval profiles, resolved server-side by id |
| `paths.py` | normalised confinement |
| `evaluation.py` | the tiered evaluator pipeline |
| `receipts.py` | the append-only tamper-evident event chain |
| `checkpointing.py` | atomic versioned checkpoints with profile re-resolution on resume |
| `session.py` | one run: admit, gate, account, checkpoint, report |

## Risk is computed, never declared

`policy.py` derives risk from what the proposal touches, what it can undo and what
it spends. A `ProposedAction` therefore cannot lower its own risk by describing
itself carefully. `contracts.py` is `extra="forbid"` and digests every field, so a
field added between the model's request and the gate's decision changes the
**digest** rather than the grant.

## Approval is required, not requested

When the gate decides an action needs an operator, promotion parks in
`WAITING_APPROVAL` with a **non-terminal** `BLOCKED` report. It neither fails the
run nor proceeds, so a parked action and a rejected one stay distinguishable by
state rather than by prose.

There is **no second complexity ceiling**. `TaskFeatures.estimated_complexity` is
*the* ceiling; `TooComplexForGovernance` and `COMPLEXITY_CEILING` were removed
rather than left to race it, because two ceilings that disagree are one ceiling
nobody can reason about.

## A tier that did not run is never a tier that passed

`evaluation.py` keeps `skipped` and `inconclusive` separate from `passed`, so a
pipeline that short-circuits on the first tier cannot be summarised as a clean
sweep. The distinction has to survive to the caller: an evaluator that never ran
and an evaluator that passed are opposite findings.

## Every attempt is receipted, denials included

`receipts.py` is an append-only tamper-evident chain over **every** attempt, and
the model cannot mint one. "What happened and why" is therefore a record rather
than a retelling, and a denial is as legible as an approval.

## Profiles re-resolve on resume

`checkpointing.py` writes atomic versioned state and re-resolves
capability/evaluator/approval profiles when a run continues. That is what stops a
checkpoint from preserving a grant that was revoked while the run was parked:
resume fails closed on **re-resolution**, not on a stored token.

`profiles.py` is default-deny by construction — three path lists, one action
allow-list and one network policy, where an **empty list denies everything**
rather than everything having to be named. `alpha_self_update` is present and
`enabled=False` with `allowed_action_types=[]` and `network="denied"`: a
capability that exists but cannot be enabled is more honest than one that is
silently reachable. Real self-update is owned by the guarded source auto-update
contract in [`../AGENTS.md`](../AGENTS.md), never by this gate.

## Budgets are ceiling-owned

`budgets.py` reserves, charges, releases and reconciles. There is no `reset` and
no `grant_more` — a run that can grant itself more budget is a run with no
ceiling — and exhaustion reports the measured figures rather than a rounded-down
remainder.

## Paths are confined by refusal, not by repair

`paths.py` normalises first and then refuses traversal, absolute, URL and symlink
escapes. A directory grant covers its subtree and nothing outside it. A path that
is merely *repaired* into confinement would let a caller discover which repairs
exist by watching which ones fire.

## The router records why, not just that

`router.py` splits a deterministic fast path from governed-run admission and
records every unmet condition as a refusal **reason**. A run therefore declines
with the list of what it needed, rather than a single opaque "no" the operator has
to reconstruct from logs.

## Lifecycle is a closed graph

`lifecycle.py` restricts transitions to a declared set. A model actor can never
enter or leave a terminal state, so a run cannot talk its way into `completed`
and cannot escape out of a terminal one.

## Boundaries that must not be crossed

- **`scorer_authority.py` stays server-owned.** A candidate may never author its
  own fitness function. That single rule is what separates a self-improvement
  loop from a self-certification one: a model grading its own variation selects
  for confidence rather than for correctness.
- **`commit_gate.py` stays the only writer of a promoted version.** Nothing in
  the governance layer may promote a variation by itself.
- **No harness-to-app dependency.** Everything here is `alpha.*`; the Gateway
  passes rows in, exactly as `company_os` and the projects boundary require.

Tests: `backend/tests/test_avo_governance.py`,
`backend/tests/test_avo_governance_layer.py`.
