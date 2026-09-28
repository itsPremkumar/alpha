### Side-effect tracking (`runtime/side_effects/`)

**The gap this closes.** Alpha already refuses to blindly replay a side effect.
`app.gateway.run_recovery.SafeRunRecoveryService` will not auto-resume a
checkpoint whose pending node is a tool, MCP, shell, browser, write/delete,
payment, or unknown node, and records `stop_reason="recovery_confirmation_required"`
instead. That is correct, and it is a **run-level** signal.

What it cannot do is name the thing that needs confirming. `stop_reason` is
attached to a *run*; the actual unknown is attached to a *specific external
effect*. "Run 42 might have charged someone" is not actionable — it can be read
once, only in the context of the run that produced it, and it cannot be
reconciled, re-listed, or worked off. "Tool call `call_abc` may have charged
someone" is actionable. That is the entire contribution of this package.

| Module | Role |
|---|---|
| `statuses.py` | `SideEffectStatus` (including `UNKNOWN`), `SideEffectLevel`, `ReconciliationVerdict`, the transition table, `SideEffectEntry` |
| `ledger.py` | `SideEffectLedger` protocol, `InMemorySideEffectLedger`, `SideEffectReclaimer` |

## The three rules that matter

**1. `UNKNOWN` is a first-class status, not an error value.** A side-effecting
operation has three honest outcomes: succeeded, failed, and *we cannot tell*.
Collapsing the third into either of the first two is how a crash becomes a double
charge. So `UNKNOWN` is durable and **enumerable** — a reconciler needs a queue,
and a support engineer needs a list.

**2. A worker that dies mid-call is the *reason* `UNKNOWN` exists.** The entry is
written `PENDING` **before** the call, becomes `IN_FLIGHT` with an owner and a
lease **before** it, and only a *live* worker may record the real outcome. An
expired lease is therefore proof that the process which would have known the
answer is gone — which is why the lease lives on the entry and not only in
memory. `reclaim_expired()` converts that to `UNKNOWN`.

`PENDING` is reclaimed too, and that is deliberate: dying in the window between
the two writes leaves the entry exactly as unknown as dying mid-call. Treating it
as "definitely nothing ran" is the optimistic guess this ledger exists to avoid.

**3. Reconciliation is not optimism.** `UNKNOWN` has exactly one exit, through
`reconcile()`, taking a `ReconciliationVerdict`. `UNDETERMINED` is a real answer
meaning "I looked and still cannot tell" and it **reopens** the entry for a later
attempt — recording it as a failure is precisely how a duplicate gets created.
`UNKNOWN -> COMPLETED` is not in the transition table at all: a worker that died
mid-call cannot be the one to decide the call succeeded.

Both settled verdicts land on `RECONCILED`, not on `COMPLETED`/`FAILED`, because
the status alone cannot express *which* outcome was established. A reader must
consult `SideEffectEntry.verdict`.

## What is deliberately absent

No module here cancels a run, resumes a run, or replays a tool call. The ledger
**records**; `SafeRunRecoveryService` still **decides**. A ledger entry nobody
reconciles is a visible, queryable gap — which is the honest outcome, not a
permission to guess.

Escalation is narrow on purpose: a *confirmed failure* on a `HIGH_RISK` or
`DESTRUCTIVE` effect escalates, because a `git_push` or `bash` that did not do
what was intended needs a person. A confirmed *success* never escalates —
somebody asked for it and it happened, which is not an incident.

## Never store arguments or results

A side-effect row is durable, is exported into support bundles, and outlives the
thread, so it stores **SHA-256 digests only**. Arguments routinely carry a
prompt, a file body, or a token. Two attempts of the same call still digest
equal, which is what a reconciler needs to compare them without keeping either
payload. `arguments_digest` / `result_digest` canonicalise with sorted keys, so
the digest does not depend on dict ordering.

`tool_call_id` is the key because it is the identifier the model, the journal, and
the stream all agree on. It is deliberately *not* an execution-ownership key —
that lesson is already learned in `alpha/subagents/` and does not need repeating.

**Where things live**:
- `runtime/side_effects/statuses.py` — the enums, `SIDE_EFFECT_STATUS_TRANSITIONS`,
  `SideEffectEntry`, `can_transition` / `validate_transition`,
  `status_for_verdict`, `verdict_is_settled`
- `runtime/side_effects/ledger.py` — `SideEffectLedger` protocol,
  `InMemorySideEffectLedger`, `SideEffectReclaimer`, `arguments_digest`,
  `result_digest`, `normalize_level`
- Tests: `tests/test_side_effect_ledger.py`
