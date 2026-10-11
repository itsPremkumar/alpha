### Alpha Completion Engine (`completion/`)

The subsystem answers one question a chat transcript cannot: **did the work
actually succeed?** It is opt-in per request — nothing in the normal chat, run,
bot or scheduler paths creates a completion scope, so enabling the engine never
taxes a turn that did not ask for completion discipline (`config.yaml ->
completion`, hot-reloaded; `mode: enforce|observe`).

It composes seams that already exist rather than replacing them:

- `RunManager` stays the sole run-lifecycle owner; this package never starts,
  terminalizes or recovers a run.
- `alpha.guardrails.command_policy` — the same policy that gates the `bash`
  tool — judges every argv, plus this package's own executable allowlist from
  `completion.preapproved_commands`.
- The store is JSON under `alpha.utils.file_lock.FileLock`: **advisory,
  single-process and restart-recoverable — never cross-process exactly-once**.
- The model-facing surface is actions on one tool, not a new tool name, so
  `BUILTIN_TOOLS` and the capability counts are unaffected.

Four pieces, which must be used together:

1. **An acceptance contract** fixed before work starts
   (`contract.build_contract` + `validate_contract`), validated
   *deterministically* so a model is never the sole validator of its own plan.
2. **A typed gate registry** (`gates`) — six reviewed check implementations and
   allowlisted arguments, never free-form shell text from a model.
3. **Evidence with freshness** (`evidence`) — a structured record of what a
   check observed, bound to its definition and inputs, so a pass from 10:00
   cannot answer for code edited at 10:02.
4. **A finalization guard** (`finalization`) that is the only route to
   `complete` — and in observe mode computes the same decision without
   applying it.

**The scope is the aggregate root.** `CompletionScope` embeds its tasks, gates,
evidence, dispatches and ownership claims; the store document's only sections
are `scopes`, `approvals`, `events` and `counters`. There is deliberately no
top-level `tasks`/`gates` table that could drift out of sync with its scope.

**One lock, one write.** Every composite operation runs as a single
`repository.mutate_scope(scope_id, fn)` whose `fn(scope, doc)` may both change
the scope and append the event (or approval) that change implies. `FileLock` is
not re-entrant, so nesting a second acquisition inside `fn` would deadlock;
passing `doc` through is what makes the atomicity achievable rather than
aspirational.

Three vocabulary rules travel through every layer and are never relaxed:

- **Returned is not verified.** A worker's prose lands in
  `DispatchRecord.state = returned`; only its own gates make it verified, and
  no route hands a task `verified` directly.
- **Not run is not passed.** `CheckOutcome.NOT_RUN`, `GateStatus.PENDING` and
  `GateStatus.WAIVED` can never satisfy a required gate. A manual review lands
  as evidence attributed to a named reviewer, never disguised as an automated
  pass.
- **Waived is not passed.** A `WAIVED` gate is a visible owner decision, which
  is why finalization treats it as a blocker.

What the engine refuses, and why, is its contract: dispatch requires a valid
contract (`ContractInvalid`, carrying every finding); status changes go through
`state_machine` (a model cannot hand itself `verified` or `complete`);
ownership conflicts are refused *before* launch (coordination, not isolation);
budget ceilings are refused with the measured numbers named, never clamped;
and an unreadable ledger is `StoreUnreadable` (HTTP 503), never an empty list.

Layering, bottom to top:

```
storage -> repository -> (models | state_machine | policy)
-> contract / ownership / scheduler / dispatch / approvals
-> gates -> evidence / verifier -> reconciliation -> finalization -> service
```

`service.py` is the facade the Gateway router and the model-facing tool both
call, and it is **deliberately synchronous**: callers reach it through
`asyncio.to_thread`, because a blocking call on the event loop stalls every
other run in the process.

Nothing here imports `app.*`: the harness must not depend on the Gateway
(`tests/test_harness_boundary.py`).

Gateway surface: `app/gateway/routers/completion.py`; contract and
refusal-to-status mapping in `errors.py`; tests:
`tests/test_completion_contract.py`, `test_completion_state.py`,
`test_completion_gates.py`, `test_completion_integration.py`,
`test_completion_critic_wiring.py`, `test_completion_router.py`.
