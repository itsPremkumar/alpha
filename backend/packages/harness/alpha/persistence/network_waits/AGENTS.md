### Durable network waits (`persistence/network_waits/`, `runtime/network/wait_registry.py`)

A **network wait** is one *parked session*: a run that needed connectivity, lost
it, and is waiting for the link to return. The row makes that fact survive a
process restart.

**Why it exists.** `alpha.runtime.network` knows the link is down, and
`alpha.runtime.sessions` has the vocabulary for "alive but parked", but between
them a parked session was re-derived from live signals on every boot. That is
fine while the process lives and useless across a restart: a task that parked on
a dead network at 02:00 and whose machine rebooted at 02:01 had no record that it
was ever waiting. This is the join.

**It records where the work got to, nothing more.** Park and resume remain the
decision of `app.gateway.run_recovery.SafeRunRecoveryService` through the normal
`start_run` path. `NetworkWaitService` takes the launcher as an injected callable
precisely so a network-aware resumer cannot acquire a harness→app import edge and
quietly bypass that service's fail-closed side-effect guarantee.

## The four properties that matter

**1. The resume is refused while the link is still down.** A wait exists *because*
the link was gone, so attempting a resume now would spend the attempt budget on a
certainty. `UNKNOWN` and `DEGRADED` still permit an attempt, for the same reason
the monitor does — not knowing is not knowing the link is down.

**2. The backoff is durable.** `next_attempt_at` is written when a resume
*fails*, not computed in memory, so a reboot cannot turn a five-minute backoff
into a hot retry loop against a link that is still down. Deadlines cross the
store boundary as **relative seconds**, never timestamps, so the store owns the
conversion and the service never has to know whether it is talking to SQL or to a
test double.

**3. Attempts are bounded, and exhaustion is reported.** `max_attempts` ends the
wait as `gave_up` with a reason. A session retried forever against a link that
never returns is the outage equivalent of the restart loop
`alpha.runtime.supervisor` refuses to write.

**4. A declined checkpoint is settled, not retried.** When the recovery owner
refuses (most likely a side-effect-unsafe checkpoint) the wait becomes `gave_up`
immediately: that checkpoint's safety will not change, so retrying is not
persistence, it is a loop with extra steps.

## Concurrency

- **One open wait per thread**, enforced twice: the partial unique index
  `uq_network_waits_thread_open` is the backstop, and `park()` reads the existing
  open row instead of raising a driver error at a caller that was only trying to
  record a fact. A thread has one active run, so two open rows would mean two
  continuations racing for it — the durable sibling of `uq_runs_thread_active`,
  extended to parked work. Settled rows accumulate freely; only *open* rows are
  constrained.
- **A resume is claimed, not assumed.** `claim_due` is a conditional
  `UPDATE ... WHERE state='waiting' AND next_attempt_at <= now`, so two gateway
  instances cannot both take the same row: the second finds it already `resuming`.
- **A failed resume is released, not lost.** `release()` returns the row to
  `waiting` with the next backoff already written and the attempt count
  preserved, so a crash between claim and launch cannot strand it. A pass that
  died *while* holding the claim is recovered by `reclaim_expired_leases()`.
- **One pass claims a bounded number** (`max_claims_per_pass`) so a large backlog
  does not stampede the provider the instant the link returns.

`state` is a closed vocabulary (`waiting` / `resuming` / `resumed` / `completed` /
`gave_up`), so "is this still waiting or has it been settled?" is a database
question, not a convention.

## Migration

`migrations/versions/0026_network_waits.py` creates the table, `ix_network_waits_due`
(the `(state, next_attempt_at)` scan the recovery pass runs each tick) and
`uq_network_waits_thread_open`. It is purely additive: no other table gains a
column, no data is backfilled, and an old binary that does not know the table
keeps working.

Both indexes are declared in the **ORM** `__table_args__` as well as the
migration, because the empty-database bootstrap path runs `create_all` +
`stamp head` and never executes the revision. `NetworkWaitRow` must stay imported
from `alpha.persistence.models` (the registration entry point) for the same
reason — `tests/test_persistence_bootstrap.py::test_create_all_and_alembic_upgrade_produce_same_schema`
fails closed if the two paths drift.

**Where things live**:
- `persistence/network_waits/model.py` — `NetworkWaitRow`,
  `OPEN_NETWORK_WAIT_STATES`, `TERMINAL_NETWORK_WAIT_STATES`
- `persistence/network_waits/sql.py` — `NetworkWaitRepository` (`park`,
  `claim_due`, `release`, `mark_terminal`, `reclaim_expired_leases`)
- `runtime/network/wait_registry.py` — `NetworkWaitService`, `NetworkWaitPolicy`,
  `NetworkWaitStore` protocol, `ParkOutcome`, `ResumeOutcome`, `NetworkWaitStatus`
- Tests: `tests/test_network_wait_registry.py` (in-memory store double for the
  service contract, real SQLite for the repository, and the ORM/migration
  predicate agreement)
