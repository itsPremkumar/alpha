### Durable side-effect ledger (`persistence/side_effects/`)

One row per external effect the agent attempted, so an effect whose worker died is
*findable* rather than invisible. The transition vocabulary and its rules live in
:mod:`alpha.runtime.side_effects.statuses`; this package persists them and is the
SQL implementation of :class:`alpha.runtime.side_effects.ledger.SideEffectLedger`.

**What SQL adds that the in-memory reference cannot promise.** The reference
implementation pins the *semantics* — the lease, the reclaim, the `UNKNOWN`-only
exit. This package pins that those semantics hold **across processes**.

Every state change is an `UPDATE ... WHERE <key> AND status = <expected>` rather
than a read-modify-write, and the affected-row count is the answer to "did my
transition apply?". That is what makes the ledger safe with two gateway instances
observing the same effect:

- `begin` relies on the primary key (`tool_call_id`), so a duplicate announce
  collides instead of creating a second row for one call — across processes, not
  just within one ledger instance.
- `mark_in_flight` / `complete` / `fail` are guarded by the status the caller
  believed it was in. A reaper that already moved the row to `UNKNOWN` wins, and
  the late worker gets `SideEffectTransitionLost` instead of silently overwriting
  an unknown with a guess.
- `reconcile` is guarded on `UNKNOWN`, so two reconcilers cannot both settle an
  entry and a settled entry cannot be re-settled.

That last point is the whole design: **the process that would have known the
answer is the one that died**, so a later worker has no standing to decide. It can
record a transition it was already mid-way through, and nothing more.

**Two different exceptions, and the difference matters.**
`IllegalSideEffectTransition` is the *deterministic* refusal, raised from
`validate_transition` before the database is touched (`UNKNOWN -> COMPLETED`, or
any transition out of a settled state). `SideEffectTransitionLost` is the *race*
— the row moved between the read and the conditional write. Collapsing them would
lose the distinction between "this is never allowed" and "somebody got there
first".

## `list_unknown` is narrower than "open"

`list_unknown` filters on `UNKNOWN` **alone**, deliberately narrower than
`OPEN_LEDGER_STATUSES` (`{unknown, reconciled}`). A `reconciled` row is open in
the sense that its history is still live, but it no longer needs attention —
`SideEffectEntry.needs_reconciliation` is `UNKNOWN` alone. The two must agree, or a
reconciler is handed a queue of already-settled work. The stored vocabularies
(`LEDGER_STATUSES`, `OPEN_LEDGER_STATUSES`, `RECLAIMABLE_LEDGER_STATUSES`) are
declared here as plain strings so a query need not import the harness package, and
a test asserts each one equals the corresponding harness enum so they cannot
drift.

## Never store arguments or results

A ledger row is durable, lands in support bundles, and outlives the thread, so it
holds **SHA-256 digests only**. Arguments routinely carry a prompt, a file body,
or a token. Two attempts of the same call still digest equal, which is what a
reconciler needs. `tests/test_side_effect_ledger_sql.py` asserts a secret passed
as an argument does not appear in any persisted row.

## Schema

`migrations/versions/0027_side_effect_ledger.py` chains after `0026_network_waits`
and is purely additive: no other table gains a column, no data is backfilled, and
an old binary that does not know the table keeps working.

Four indexes, all declared in the **ORM** `__table_args__` as well as the
migration because the empty-database bootstrap path runs `create_all` +
`stamp head` and never executes the revision:

| Index | Why |
|---|---|
| `ix_tool_side_effects_unknown` | the reconciliation queue — the `(status, updated_at)` lookup an operator runs |
| `ix_tool_side_effects_lease` | the reclaim scan, over in-flight rows whose owner stopped reporting |
| `ix_tool_side_effects_thread_status` / `ix_tool_side_effects_run_status` | per-scope audits in one index range scan |

**Two bootstrap gates apply to any new table here and will fail closed:**

1. `ToolSideEffectRow` must stay imported from `alpha.persistence.models` (the
   registration entry point), or `create_all` does not see it while the alembic
   chain does.
2. A column with a `server_default` in the migration must declare the same
   `server_default` in the ORM, and vice versa. `status`, `level` and `attempt`
   carry it on both sides. A Python-side `default` alone is not equivalent, and
   `tests/test_persistence_bootstrap.py::test_create_all_and_alembic_upgrade_produce_same_schema`
   reports the drift as `server_default drift create_all=None alembic="..."`.

**Where things live**:
- `persistence/side_effects/model.py` — `ToolSideEffectRow`, `LEDGER_STATUSES`,
  `OPEN_LEDGER_STATUSES`, `RECLAIMABLE_LEDGER_STATUSES`
- `persistence/side_effects/sql.py` — `SqlSideEffectLedger`, `SideEffectTransitionLost`
- Tests: `tests/test_side_effect_ledger_sql.py` (real SQLite, the cross-process
  properties), `tests/test_side_effect_ledger.py` (the in-memory reference and the
  state machine)
