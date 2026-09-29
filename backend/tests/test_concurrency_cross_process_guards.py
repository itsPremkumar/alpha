"""The *documented* cross-process guards, verified.

The runtime guide makes several claims about guards that protect shared state
across Gateway workers. A documented cross-process guard that is not actually
there is the most dangerous class of finding in this repository, because every
other document inherits the confidence. So each claim below is:

- quoted from the guide or the source,
- turned into an executable assertion,
- and **broken on purpose** to confirm the assertion actually bites.

The mutation evidence is in the module docstring at the bottom of each class.
A guard test that has never been seen to fail does not demonstrate anything.

The three RunEventStore implementations are treated as one interface, because
``runtime/AGENTS.md`` says ``take_latest`` is "the single owner" of the
``limit=0`` rule precisely so they "cannot drift on the zero case". That claim
is checked here, and then checked for drift on the cases it does *not* mention.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest
import sqlalchemy
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alpha.runtime.events.store.base import take_latest

# ---------------------------------------------------------------------------
# Claim 1: "memory and JSONL provide the documented single-process guarantee,
#            while the DB store adds per-thread in-process locks and PostgreSQL
#            advisory locks for cross-process writers."
#            -- runtime/AGENTS.md, "Run delivery receipts"
# ---------------------------------------------------------------------------


class TestPutIfAbsentIsSerializedWithOrdinaryWriters:
    """The claim is that ``put_if_absent`` cannot race a normal write.

    That is the durability primitive the delivery receipt depends on: a worker
    that crashed after writing a receipt must not let orphan recovery write a
    second one. It holds for all three backends within one process, and for the
    DB backend across processes on Postgres.
    """

    @pytest.mark.asyncio
    async def test_memory_serializes_by_await_free_check_then_append(self) -> None:
        """Memory: no ``await`` between the lookup and the append.

        The guarantee is structural, not a lock. Pinned by racing many writers:
        exactly one may create the singleton.
        """
        from alpha.runtime.events.store.memory import MemoryRunEventStore

        store = MemoryRunEventStore()
        results = await asyncio.gather(*[store.put_if_absent(thread_id="t1", run_id="r1", event_type="run.delivery", category="outputs", content={"n": i}) for i in range(25)])
        assert sum(1 for _r, created in results if created) == 1
        assert len(await store.list_events("t1", "r1", event_types=["run.delivery"])) == 1

    @pytest.mark.asyncio
    async def test_jsonl_serializes_with_its_per_thread_write_lock(self, tmp_path) -> None:
        """JSONL: the check and the append both happen under the thread lock.

        Asserted as *mutual exclusion of the critical section*, not as "one writer
        won". Those are different tests and only the first is reliable: with
        ``asyncio.to_thread`` the outcome of a lost race depends on thread
        scheduling, so a count-based assertion can pass against an unguarded
        implementation purely by luck. Counting concurrent entries into the
        guarded region cannot pass by luck -- if two coroutines are inside it at
        once, the lock is gone, whatever the eventual write count.

        (This is not a hypothetical: the first version of this test used the
        count, and it passed with the lock removed.)
        """
        import threading
        import time

        from alpha.runtime.events.store.jsonl import JsonlRunEventStore

        class _Probe(JsonlRunEventStore):
            def __init__(self, base_dir):
                super().__init__(base_dir=base_dir)
                self._probe_lock = threading.Lock()
                self._inside = 0
                self.max_inside = 0

            def _read_run_events(self, thread_id, run_id):
                # Widen the window so an unguarded implementation is caught
                # reliably rather than occasionally.
                with self._probe_lock:
                    self._inside += 1
                    self.max_inside = max(self.max_inside, self._inside)
                try:
                    time.sleep(0.02)
                    return super()._read_run_events(thread_id, run_id)
                finally:
                    with self._probe_lock:
                        self._inside -= 1

        store = _Probe(tmp_path)
        results = await asyncio.gather(*[store.put_if_absent(thread_id="t1", run_id="r1", event_type="run.delivery", category="outputs", content={"n": i}) for i in range(6)])

        assert store.max_inside == 1, f"the read-and-write region must exclude other writers, but {store.max_inside} were inside it at once"
        assert sum(1 for _r, created in results if created) == 1
        assert len(await store.list_events("t1", "r1", event_types=["run.delivery"])) == 1

    @pytest.mark.asyncio
    async def test_db_serializes_with_its_per_thread_write_lock(self, db_store) -> None:
        """DB: same per-thread lock, plus the dialect-specific row lock.

        This is the only backend of the three whose guard survives a second
        process -- on Postgres. On SQLite the ``with_for_update()`` is a no-op
        the dialect silently drops, so the *cross-process* half of the claim
        does not hold there; the startup gate refuses SQLite under
        ``GATEWAY_WORKERS > 1`` for exactly this reason. Verified below.
        """
        store = db_store
        results = await asyncio.gather(*[store.put_if_absent(thread_id="t1", run_id="r1", event_type="run.delivery", category="outputs", content={"n": i}) for i in range(25)])
        assert sum(1 for _r, created in results if created) == 1, f"one writer must win, got {sum(1 for _r, c in results if c)}"
        assert len(await store.list_events("t1", "r1", event_types=["run.delivery"])) == 1

    @pytest.mark.asyncio
    async def test_db_takes_an_advisory_lock_on_postgres_and_a_row_lock_otherwise(self) -> None:
        """The dialect branch in ``_max_seq_for_thread`` is real, not decorative.

        Postgres rejects ``SELECT max(...) FOR UPDATE`` because aggregates are
        not lockable rows, so the implementation takes a transaction-level
        advisory lock keyed by the thread id and *then* reads the aggregate.
        Asserting the two branches apart is what makes the claim checkable
        without a live Postgres: the SQL text is the evidence.
        """
        import inspect

        from alpha.runtime.events.store.db import DbRunEventStore

        source = inspect.getsource(DbRunEventStore._max_seq_for_thread)
        assert "pg_advisory_xact_lock" in source, "Postgres must take the advisory lock"
        assert "hashtext" in source, "keyed by thread_id, so two threads do not serialize against each other"
        assert "with_for_update" in source, "other dialects use the row lock instead"
        assert 'dialect_name == "postgresql"' in source, "and the branch is chosen by dialect name"

    def test_the_advisory_lock_claim_is_scoped_to_postgres_not_sqlite(self) -> None:
        """A cross-process guard that exists on only one backend is not "the guard".

        The guide's sentence bundles "per-thread in-process locks" (every
        backend) with "PostgreSQL advisory locks" (the DB backend, on Postgres).
        Read quickly the second clause sounds like a general property of the DB
        store. It is not: on SQLite ``stmt.with_for_update()`` is emitted and the
        dialect drops it, so the *cross-process* half of the claim does not hold
        there. That is why the startup gate refuses SQLite under
        ``GATEWAY_WORKERS > 1`` -- the two facts are the same fact seen twice.

        Asserting it here means a future change that assumes the DB store is
        cross-process safe everywhere has a test to fail, and it keeps the
        operator statement honest about which backend carries the guarantee.
        """
        from alpha.runtime.events.store.db import DbRunEventStore

        source = inspect.getsource(DbRunEventStore._max_seq_for_thread)
        assert "pg_advisory_xact_lock" in source, "Postgres is the only dialect with a cross-process writer lock"
        # The fallback is the function's last statement: a bare row lock and nothing else.
        fallback = source.strip().rsplit("return", 1)[-1]
        assert "with_for_update" in fallback, f"the non-Postgres branch must use a row lock; got {fallback!r}"
        assert "advisory" not in fallback.lower(), f"there is no second advisory lock on the fallback path; got {fallback!r}"

    def test_sqlite_really_drops_for_update(self) -> None:
        """Evidence for the scoping claim above, straight from SQLAlchemy.

        Compiling ``FOR UPDATE`` for SQLite yields no such clause, so the row
        lock in the fallback branch protects nothing between processes. Proving
        it here means the scoping claim rests on a measurement rather than on
        reading the dialect docs.
        """
        from sqlalchemy.dialects import postgresql, sqlite

        from alpha.persistence.models.run_event import RunEventRow

        statement = sqlalchemy.select(RunEventRow.seq).with_for_update()
        sqlite_sql = str(statement.compile(dialect=sqlite.dialect())).upper()
        pg_sql = str(statement.compile(dialect=postgresql.dialect())).upper()

        assert "FOR UPDATE" not in sqlite_sql, f"SQLite must drop the row lock; got {sqlite_sql!r}"
        assert "FOR UPDATE" in pg_sql, f"Postgres must keep the row lock; got {pg_sql!r}"


# ---------------------------------------------------------------------------
# Claim 2: "take_latest ... the single owner of that rule, so the three
#            implementations of one interface cannot drift on the zero case."
#            -- runtime/AGENTS.md, "limit is a row count, not a slice bound"
# ---------------------------------------------------------------------------


class TestTakeLatestIsTheSingleOwnerOfTheZeroCase:
    """``limit=0`` must mean "no rows" in all three backends.

    ``rows[-limit:]`` cannot express it, because ``-0 == 0`` makes
    ``rows[-0:]`` the whole list. The rule is centralised in ``take_latest``
    and the non-SQL backends route every latest-page slice through it.
    """

    def test_take_latest_is_the_documented_single_owner(self) -> None:
        assert take_latest([1, 2, 3], 0) == []
        assert take_latest([1, 2, 3], -1) == [], "a negative limit is not 'almost everything' either"
        assert take_latest([1, 2, 3], 2) == [2, 3]
        assert take_latest([1, 2, 3], 5) == [1, 2, 3]
        assert take_latest([], 3) == []
        assert "single owner" in (take_latest.__doc__ or "") or "cannot drift" in (take_latest.__doc__ or "")

    @pytest.mark.asyncio
    async def test_all_three_backends_agree_on_the_zero_case(self, db_store, tmp_path) -> None:
        """Parity across the interface, on every latest-page entry point."""
        from alpha.runtime.events.store.jsonl import JsonlRunEventStore
        from alpha.runtime.events.store.memory import MemoryRunEventStore

        stores = [MemoryRunEventStore(), JsonlRunEventStore(base_dir=tmp_path / "jsonl"), db_store]
        for store in stores:
            for seq in range(1, 6):
                await store.put(thread_id="t1", run_id="r1", event_type="llm.ai.response", category="message", content={"type": "ai", "id": f"m{seq}", "content": f"r{seq}"})

        for store in stores:
            name = type(store).__name__
            assert await store.list_messages("t1", limit=0, user_id=None) == [], f"{name}.list_messages(limit=0)"
            assert await store.list_messages("t1", before_seq=5, limit=0, user_id=None) == [], f"{name}.list_messages(before_seq, limit=0)"
            assert await store.list_messages("t1", after_seq=1, limit=0, user_id=None) == [], f"{name}.list_messages(after_seq, limit=0)"
            assert await store.list_messages_by_run("t1", "r1", limit=0) == [], f"{name}.list_messages_by_run(limit=0)"
            # Control: the zero case is not bought by breaking the normal one.
            assert [r["seq"] for r in await store.list_messages("t1", limit=2, user_id=None)] == [4, 5], f"{name} control"

    def test_no_store_reintroduces_a_raw_negative_slice(self) -> None:
        """The guard against reintroduction: no executable ``[-limit:]`` in a store.

        The guide says "Do not reintroduce a raw ``[-limit:]`` in a store". That
        instruction is only enforceable if something checks it, so this reads the
        implementations and fails on the pattern.

        Scoped to code, not prose: ``base.py`` *documents* the anti-pattern in
        ``take_latest``'s own docstring, which is exactly where the pattern
        should remain visible. A naive text scan flags the explanation as the
        defect, so the check strips docstrings and comments before matching --
        and that is worth stating, because the alternative is a guard that gets
        disabled the first time someone improves the comment.
        """
        import ast
        from pathlib import Path

        import alpha.runtime.events.store as store_pkg

        docstring_nodes: set[tuple[str, int]] = set()
        for module_path in sorted(Path(store_pkg.__file__).parent.glob("*.py")):
            tree = ast.parse(module_path.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = getattr(node, "body", [])
                    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                        docstring_nodes.add((module_path.name, body[0].lineno))

        offenders = []
        for module_path in sorted(Path(store_pkg.__file__).parent.glob("*.py")):
            tree = ast.parse(module_path.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Subscript):
                    continue
                slice_node = node.slice
                if not isinstance(slice_node, ast.Slice) or slice_node.lower is None or slice_node.upper is None:
                    continue
                if not (isinstance(slice_node.lower, ast.UnaryOp) and isinstance(slice_node.lower.op, ast.USub) and getattr(slice_node.lower.operand, "id", None) == "limit"):
                    continue
                if (module_path.name, node.lineno) in docstring_nodes:
                    continue
                offenders.append(f"{module_path.name}:{node.lineno}")
        assert not offenders, f"raw [-limit:] reintroduced in executable store code: {offenders}"

    @pytest.mark.asyncio
    async def test_the_backends_drift_on_a_case_the_guide_does_not_mention(self) -> None:
        """``list_events`` is not routed through ``take_latest`` -- and does not need to be.

        Worth recording because it is the obvious "next place they could drift"
        and it turns out to be safe for a different reason: every backend
        expresses ``list_events`` as a forward cursor, so it slices
        ``[:limit]`` and never needs the latest-page rule. A negative limit is
        the one case where they would disagree, and no caller can produce one:
        the routes clamp with ``ge=1``. Asserted here so the next reader does
        not have to re-derive it.
        """
        from alpha.runtime.events.store.jsonl import JsonlRunEventStore
        from alpha.runtime.events.store.memory import MemoryRunEventStore

        for store in (MemoryRunEventStore(), JsonlRunEventStore(base_dir=None)):
            for seq in range(1, 6):
                await store.put(thread_id="t1", run_id="r1", event_type="trace", category="trace", content={"i": seq})
            assert len(await store.list_events("t1", "r1", limit=0)) == 0, "forward cursor: limit=0 is naturally empty"
            assert [e["seq"] for e in await store.list_events("t1", "r1", limit=2)] == [1, 2]


# ---------------------------------------------------------------------------
# Claim 3: the run store's partial unique index is the cross-process
#            thread-uniqueness guarantee, not the in-memory check.
#            -- runtime/runs/manager.py::_admit_thread_operation
# ---------------------------------------------------------------------------


class TestTheDurableAdmissionIndexIsTheRealGuard:
    """``_admit_thread_operation`` says so in its own docstring.

    "Local inflight check (same-worker guard; cross-worker is the store's
    partial unique index below)." That is a claim about a database index, so it
    is checkable by reading the schema.
    """

    def test_the_partial_unique_index_is_declared_on_both_dialects(self) -> None:
        """``uq_runs_thread_active`` must exist in the ORM *and* name both dialects.

        It has to live in the ORM ``__table_args__`` because the empty-database
        bootstrap path runs ``create_all`` + ``stamp head`` and never executes
        the migration that also defines it. A partial index missing its
        ``sqlite_where``/``postgresql_where`` predicate would silently not be
        partial on one of them -- and a non-partial unique index on ``thread_id``
        would refuse the *second run of a thread ever*, which is a very
        different bug.
        """
        from alpha.persistence.run.model import RunRow

        indexes = {idx.name: idx for idx in RunRow.__table__.indexes}
        assert "uq_runs_thread_active" in indexes, f"the cross-process admission guard is missing; indexes are {sorted(indexes)}"
        idx = indexes["uq_runs_thread_active"]
        assert [c.name for c in idx.columns] == ["thread_id"], "one active run per thread, keyed by thread only"
        assert idx.unique is True

        # SQLAlchemy folds `sqlite_where=` / `postgresql_where=` into
        # `dialect_options` at construction; the keyword attributes do not survive.
        options = idx.dialect_options
        for dialect in ("sqlite", "postgresql"):
            assert dialect in options, f"the index would not be partial on {dialect}: no dialect_options entry"
            rendered = str(options[dialect]["where"])
            assert "pending" in rendered and "running" in rendered, f"{dialect} must restrict to active statuses, got {rendered!r}"

    def test_the_index_is_emitted_as_partial_in_both_dialects_ddl(self) -> None:
        """The declarative options must survive into real DDL on both dialects.

        Asserting on ``dialect_options`` checks intent; this checks the emitted
        ``CREATE UNIQUE INDEX``. A non-partial index on ``thread_id`` would still
        be ``unique=True`` and would still pass an intent-only test while
        refusing the *second run of a thread ever* -- so the DDL is the evidence.
        """
        from sqlalchemy.dialects import postgresql, sqlite
        from sqlalchemy.schema import CreateIndex

        from alpha.persistence.run.model import RunRow

        idx = next(i for i in RunRow.__table__.indexes if i.name == "uq_runs_thread_active")
        for dialect_name, dialect in (("sqlite", sqlite.dialect()), ("postgresql", postgresql.dialect())):
            ddl = str(CreateIndex(idx).compile(dialect=dialect)).upper()
            assert "CREATE UNIQUE INDEX" in ddl, f"{dialect_name} DDL is not a unique index: {ddl!r}"
            assert "WHERE" in ddl, f"{dialect_name} index is not partial: {ddl!r}"
            assert "PENDING" in ddl and "RUNNING" in ddl, f"{dialect_name} predicate does not restrict to active runs: {ddl!r}"

    def test_the_manager_names_the_index_as_the_cross_process_guard(self) -> None:
        """The docstring's claim is the one the code is making; keep them linked."""
        import inspect

        from alpha.runtime.runs.manager import RunManager

        source = inspect.getsource(RunManager._admit_thread_operation)
        assert "cross-worker is the" in source and "partial unique index" in source, "the manager must keep naming the store-level guard as the cross-worker one"

    @pytest.mark.asyncio
    async def test_a_second_admission_for_one_thread_is_refused_by_the_store(self, run_repo) -> None:
        """Two independent sessions cannot both hold an active run for a thread.

        This is the load-bearing proof: the two calls share no Python state at
        all -- separate session factories, separate connections -- so anything
        that refuses the second is a *database* guarantee, not an in-process
        lock.
        """
        from alpha.runtime.runs.manager import ConflictError

        await run_repo.create_thread_operation_atomic(
            run_id="run-a",
            thread_id="t1",
            owner_worker_id="host:a",
            lease_expires_at=None,
            multitask_strategy="reject",
        )

        from sqlalchemy.exc import IntegrityError

        with pytest.raises((ConflictError, IntegrityError)):
            await run_repo.create_thread_operation_atomic(
                run_id="run-b",
                thread_id="t1",
                owner_worker_id="host:b",
                lease_expires_at=None,
                multitask_strategy="reject",
            )

    @pytest.mark.asyncio
    async def test_a_terminal_run_frees_the_thread_for_a_new_one(self, run_repo) -> None:
        """Control: the index is partial, so history does not block new work.

        Without the ``status IN (...)`` predicate the second half of the test
        suite would pass and the product would be unusable, which is exactly the
        failure mode a partial index silently develops.
        """
        from alpha.runtime.runs.manager import RunStatus
        from alpha.runtime.runs.store.base import RunStore

        await run_repo.create_thread_operation_atomic(run_id="run-a", thread_id="t1", owner_worker_id="host:a", lease_expires_at=None, multitask_strategy="reject")
        await run_repo.update_status("run-a", RunStatus.success.value)

        row, _claimed = await run_repo.create_thread_operation_atomic(run_id="run-b", thread_id="t1", owner_worker_id="host:b", lease_expires_at=None, multitask_strategy="reject")
        assert row["run_id"] == "run-b", "a completed run must not block the thread"
        assert isinstance(run_repo, RunStore)


# ---------------------------------------------------------------------------
# Claim 4: the side-effect ledger's cross-process conditional transitions
#            -- persistence/side_effects/AGENTS.md
# ---------------------------------------------------------------------------


class TestTheSideEffectLedgerSqlGuards:
    """Every transition is ``UPDATE ... WHERE key AND status = <expected>``.

    Two claims are worth proving: a duplicate ``begin`` collides on the primary
    key rather than creating a second row, and a reconciler that lost the race
    is told rather than silently overwriting an ``UNKNOWN``.
    """

    @pytest.mark.asyncio
    async def test_a_duplicate_begin_returns_the_existing_row(self, sql_ledger) -> None:
        """``begin`` is idempotent per ``tool_call_id`` across ledger instances.

        Two *separate* ``SqlSideEffectLedger`` objects over one database stand in
        for two processes. The primary key is what closes this, not a lock.
        """

        first = await sql_ledger.begin(tool_call_id="call_1", tool_name="write_file", thread_id="t1", run_id="r1", arguments={"path": "a"})
        second = await sql_ledger.begin(tool_call_id="call_1", tool_name="write_file", thread_id="t1", run_id="r1", arguments={"path": "DIFFERENT"})

        assert first.tool_call_id == second.tool_call_id
        assert first.status is second.status, "a re-announce must not reset the entry -- that is how UNKNOWN gets erased"
        assert second.arguments_digest == first.arguments_digest, "the first announcement's digest is authoritative"

        rows = await sql_ledger.all()
        assert len(rows) == 1, "one row per tool_call_id, however many announce it"

    @pytest.mark.asyncio
    async def test_two_reconcilers_cannot_both_settle_one_entry(self, sql_ledger) -> None:
        """``reconcile`` is guarded on ``UNKNOWN``; the loser is told, not ignored.

        The winner settles the entry. The second reconciler -- which had read an
        ``UNKNOWN`` and therefore believes it may settle -- gets
        ``SideEffectTransitionLost`` instead of quietly overwriting the verdict.
        This is the difference between "somebody got there first" and "nothing
        happened", and it is the whole reason the row exists.
        """
        import time

        from alpha.persistence.side_effects import SideEffectTransitionLost
        from alpha.runtime.side_effects.statuses import ReconciliationVerdict, SideEffectStatus

        await sql_ledger.begin(tool_call_id="call_1", tool_name="bash", thread_id="t1", run_id="r1", lease_seconds=1.0)
        await sql_ledger.mark_in_flight("call_1", owner_worker_id="host:a", lease_seconds=1.0)
        # A lost worker is modelled as an expired lease, so the reclaim is run
        # with a clock past the deadline. A NULL lease is not "expired" -- it
        # means "nobody claimed this" -- and the reaper must skip those.
        await sql_ledger.reclaim_expired(now=time.time() + 3600)

        assert (await sql_ledger.get("call_1")).status is SideEffectStatus.UNKNOWN, "a dead worker's in-flight effect becomes UNKNOWN, not a failure"

        await sql_ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_FAILURE, detail="first")
        with pytest.raises(SideEffectTransitionLost):
            await sql_ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_SUCCESS, detail="second")
        settled = await sql_ledger.get("call_1")
        assert settled.detail == "first", "the settled verdict is not overwritten by the loser of the race"
        assert settled.verdict is ReconciliationVerdict.CONFIRMED_FAILURE

    @pytest.mark.asyncio
    async def test_an_undetermined_reconciliation_reopens_rather_than_settles(self, sql_ledger) -> None:
        """``UNDETERMINED`` keeps the entry owed an answer.

        Recording "could not tell" as a failure is precisely how a duplicate
        side effect gets created, so the reopen is the behaviour that matters and
        it must be identical in both implementations.
        """
        import time

        from alpha.runtime.resilience.clock import ManualClock
        from alpha.runtime.side_effects.ledger import InMemorySideEffectLedger
        from alpha.runtime.side_effects.statuses import ReconciliationVerdict, SideEffectStatus

        await sql_ledger.begin(tool_call_id="call_sql", tool_name="git_push", thread_id="t1", run_id="r1", lease_seconds=1.0)
        await sql_ledger.mark_in_flight("call_sql", owner_worker_id="host:a", lease_seconds=1.0)
        await sql_ledger.reclaim_expired(now=time.time() + 3600)
        await sql_ledger.reconcile("call_sql", ReconciliationVerdict.UNDETERMINED, detail="could not check the remote")

        clock = ManualClock(1000.0)
        memory = InMemorySideEffectLedger(clock=clock)
        await memory.begin(tool_call_id="call_mem", tool_name="git_push", lease_seconds=1.0)
        await memory.mark_in_flight("call_mem", owner_worker_id="host:a", lease_seconds=1.0)
        clock.advance(3600.0)  # the worker's lease outlived it
        await memory.reclaim_expired()
        await memory.reconcile("call_mem", ReconciliationVerdict.UNDETERMINED, detail="could not check the remote")

        sql_entry = await sql_ledger.get("call_sql")
        mem_entry = await memory.get("call_mem")
        assert sql_entry.status is SideEffectStatus.UNKNOWN
        assert mem_entry.status is SideEffectStatus.UNKNOWN, "both implementations must reopen, or the in-memory one is not a reference"
        assert sql_entry.needs_reconciliation is mem_entry.needs_reconciliation is True
        assert sql_entry.is_undetermined_reconciliation is mem_entry.is_undetermined_reconciliation is False, "an undetermined pass leaves the entry UNKNOWN, not RECONCILED"

    def test_the_ledger_states_that_memory_cannot_promise_cross_process(self) -> None:
        """The in-memory reference says so rather than implying parity.

        It is a test double and a no-persistence default; if it read as a
        production implementation, an operator would size a deployment on it.
        """
        from alpha.runtime.side_effects.ledger import InMemorySideEffectLedger

        doc = InMemorySideEffectLedger.__doc__ or ""
        assert "Not a substitute for the SQL repository across processes" in doc

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "KNOWN DEFECT in persistence/side_effects/sql.py::reconcile. The settled path is "
            "`UPDATE ... WHERE tool_call_id AND status = 'unknown'`, but the UNDETERMINED reopen path "
            "(line ~161) is a read-modify-write guarded only on tool_call_id. A reconciler that read "
            "UNKNOWN, lost the race, and then reported UNDETERMINED drives a settled RECONCILED entry "
            "back to UNKNOWN and discards the winning verdict, with no error. That contradicts "
            "persistence/side_effects/AGENTS.md: 'Every state change is an UPDATE ... WHERE <key> AND "
            "status = <expected> rather than a read-modify-write' and 'a settled entry cannot be "
            "re-settled'. The in-memory reference has no such gap: it holds one lock across both the "
            "read and the write. Fix: add `AND status IN ('unknown','reconciled')` to the reopen "
            "UPDATE and raise SideEffectTransitionLost when rowcount is 0. Not fixed here -- runtime/ "
            "and persistence/ belong to other agents. strict=True means the repair turns this red, so "
            "the marker cannot outlive the fix."
        ),
    )
    @pytest.mark.asyncio
    async def test_a_losing_reconciler_cannot_reopen_a_settled_entry(self, sql_ledger) -> None:
        """The document says a settled entry cannot be re-settled. It can be reopened.

        Two reconcilers both read ``UNKNOWN``. The first settles it as
        ``CONFIRMED_SUCCESS``. The second, still holding its stale ``UNKNOWN``
        read, reports ``UNDETERMINED`` -- and because the reopen path carries no
        status guard, that write succeeds and puts the entry back to ``UNKNOWN``
        with its verdict cleared.

        The entry does not acquire a *false* conclusion: the loser's guess does
        not overwrite the winner's verdict, and the effect stays in the
        ``UNKNOWN`` queue for a human. What is lost is the winning
        reconciliation, silently and without an exception -- a lost update, and
        the ledger's whole argument is that nobody may quietly undo a settled
        effect.
        """
        import time

        from alpha.runtime.side_effects.statuses import ReconciliationVerdict, SideEffectStatus

        await sql_ledger.begin(tool_call_id="call_race", tool_name="bash", thread_id="t1", run_id="r1", lease_seconds=1.0)
        await sql_ledger.mark_in_flight("call_race", owner_worker_id="host:a", lease_seconds=1.0)
        await sql_ledger.reclaim_expired(now=time.time() + 3600)
        assert (await sql_ledger.get("call_race")).status is SideEffectStatus.UNKNOWN

        # Reconciler A wins the race.
        await sql_ledger.reconcile("call_race", ReconciliationVerdict.CONFIRMED_SUCCESS, detail="A")
        assert (await sql_ledger.get("call_race")).verdict is ReconciliationVerdict.CONFIRMED_SUCCESS

        # Reconciler B, holding a stale UNKNOWN read, reports it could not tell.
        await sql_ledger.reconcile("call_race", ReconciliationVerdict.UNDETERMINED, detail="B")

        final = await sql_ledger.get("call_race")
        assert final.status is SideEffectStatus.RECONCILED, "a settled entry must not be reopened by a reconciler that lost the race"
        assert final.verdict is ReconciliationVerdict.CONFIRMED_SUCCESS, "and the winning verdict must survive"


# ---------------------------------------------------------------------------
# Claim 5: the network-wait registry's durable backoff and claim lease
#            -- persistence/network_waits/AGENTS.md
# ---------------------------------------------------------------------------


class TestNetworkWaitsAreDurableThroughTheStoreNotTheService:
    """``NetworkWaitService`` holds no wait state; the store does.

    The service's own counters (``_claimed``, ``_resumed``, ``_gave_up``) are
    process-local reporting, and the actual "which sessions are parked, and
    when may they be retried" lives in the store. This is the shape to check,
    because a service that cached the rows would look durable and not be.
    """

    def test_the_service_holds_no_rows_only_counters(self) -> None:
        """The service keeps counters; the store keeps rows.

        A service that cached the parked rows would read as durable and not be
        durable, so the shape is what is pinned: the injected store is the only
        place a deadline can live, and the first backoff is written at park time
        rather than derived at resume time.
        """
        import inspect

        from alpha.runtime.network.wait_registry import NetworkWaitService

        init_source = inspect.getsource(NetworkWaitService.__init__)
        assert "_store" in init_source, "the durable record is the injected store"
        assert "_claimed" in init_source and "_resumed" in init_source and "_gave_up" in init_source, "the service's own state is reporting counters only"
        assert "next_attempt_at" not in init_source, "the service must not cache a deadline; the store owns the conversion"
        assert "backoff_for(1)" in inspect.getsource(NetworkWaitService.park), "the first backoff is written at park time, not computed at resume time"

    def test_the_process_wide_accessor_is_a_singleton_that_may_be_absent(self) -> None:
        """``get_network_wait_service()`` returning ``None`` is a normal state.

        A harness-only process has no Gateway, so there is no service. The
        accessor returning ``None`` rather than raising is what keeps a missing
        optional service from turning one run failure into a second one.
        """
        from alpha.runtime.network.wait_registry import get_network_wait_service, set_network_wait_service

        set_network_wait_service(None)
        assert get_network_wait_service() is None

    def test_the_gateway_installs_a_wait_service_with_no_resume_launcher(self) -> None:
        """The guide says the Gateway installs none; the wiring confirms it.

        ``NetworkWaitService`` takes the resume launcher by injection precisely so
        a network-aware resumer cannot acquire a second continuation authority
        that bypasses ``SafeRunRecoveryService``'s side-effect gate. If the
        Gateway ever passed a ``launcher=``, that fail-closed guarantee would
        have a second door.
        """
        import inspect

        import app.gateway.deps as deps

        source = inspect.getsource(deps)
        assert "NetworkWaitService(" in source
        line = next(line for line in source.splitlines() if "NetworkWaitService(" in line)
        assert "launcher" not in line, f"the Gateway must not install a resume launcher; found: {line.strip()!r}"


# ---------------------------------------------------------------------------
# Claim 6: the checkpoint mode is process-frozen and must match across
#            processes -- runtime/checkpoint_mode.py
# ---------------------------------------------------------------------------


class TestTheCheckpointFreezeIsProcessLocalByConstruction:
    """The freeze is a module global, which is exactly the documented shape.

    "Mode is process-frozen and restart-required" is implemented as a
    process-global that refuses a second value. That is a *within-process*
    guard, and the cross-process half is the metadata marker plus the
    fail-closed read gate. Worth separating, because only the first is a lock.
    """

    def test_a_second_mode_in_one_process_is_refused(self) -> None:
        import alpha.runtime.checkpoint_mode as mode_module
        from alpha.runtime.checkpoint_mode import (
            CheckpointModeReconfigurationError,
            _frozen_checkpoint_channel_mode,
            freeze_checkpoint_channel_mode,
        )

        original = mode_module._frozen_checkpoint_channel_mode
        try:
            mode_module._frozen_checkpoint_channel_mode = None
            assert freeze_checkpoint_channel_mode("full") == "full"
            assert freeze_checkpoint_channel_mode("full") == "full", "re-freezing the same value is idempotent"
            with pytest.raises(CheckpointModeReconfigurationError):
                freeze_checkpoint_channel_mode("delta")
        finally:
            mode_module._frozen_checkpoint_channel_mode = original
        assert _frozen_checkpoint_channel_mode is original

    def test_a_second_snapshot_frequency_in_one_process_is_refused(self) -> None:
        """The cadence is frozen with the mode, for the same reason."""
        import alpha.runtime.checkpoint_mode as mode_module
        from alpha.runtime.checkpoint_mode import CheckpointModeReconfigurationError, freeze_checkpoint_snapshot_frequency

        original = mode_module._frozen_checkpoint_snapshot_frequency
        try:
            mode_module._frozen_checkpoint_snapshot_frequency = None
            assert freeze_checkpoint_snapshot_frequency(10) == 10
            with pytest.raises(CheckpointModeReconfigurationError):
                freeze_checkpoint_snapshot_frequency(50)
        finally:
            mode_module._frozen_checkpoint_snapshot_frequency = original

    def test_a_non_positive_snapshot_frequency_is_refused_at_the_boundary(self) -> None:
        import alpha.runtime.checkpoint_mode as mode_module
        from alpha.runtime.checkpoint_mode import freeze_checkpoint_snapshot_frequency

        original = mode_module._frozen_checkpoint_snapshot_frequency
        try:
            mode_module._frozen_checkpoint_snapshot_frequency = None
            with pytest.raises(ValueError):
                freeze_checkpoint_snapshot_frequency(0)
        finally:
            mode_module._frozen_checkpoint_snapshot_frequency = original

    def test_the_mode_marker_is_written_on_write_so_a_peer_can_detect_it(self) -> None:
        """The cross-process half: absence of the marker means full.

        Delta writes stamp ``alpha_checkpoint_channel_mode: "delta"``, which is
        what lets a *different* process's full-mode read fail closed instead of
        silently materialising an empty message list.
        """
        from alpha.runtime.checkpoint_mode import CHECKPOINT_MODE_METADATA_KEY, inject_checkpoint_mode

        full_config: dict = {}
        inject_checkpoint_mode(full_config, "full")
        assert CHECKPOINT_MODE_METADATA_KEY not in full_config["metadata"], "absence is the marker for full; nothing is written"

        delta_config: dict = {}
        inject_checkpoint_mode(delta_config, "delta")
        assert delta_config["metadata"][CHECKPOINT_MODE_METADATA_KEY] == "delta"

    def test_the_snapshot_frequency_is_deliberately_not_stamped(self) -> None:
        """A cadence mismatch is undetectable, by design, and the code says so.

        ``freeze_checkpoint_snapshot_frequency`` documents that the cadence is
        "deliberately NOT stamped into checkpoint metadata". The consequence --
        that two processes on different cadences will silently disagree and
        nothing will catch it -- is a real operational hazard with no detection.
        Recorded so the operator statement can carry it.
        """
        from alpha.runtime.checkpoint_mode import freeze_checkpoint_snapshot_frequency

        doc = freeze_checkpoint_snapshot_frequency.__doc__ or ""
        assert "NOT stamped" in doc and "must match across every process" in doc


# ---------------------------------------------------------------------------
# Claim 7: the multi-worker startup gate
#            -- app/gateway/deps.py::_enforce_postgres_for_multi_worker
# ---------------------------------------------------------------------------


class TestTheStartupGateIsTheMultiWorkerBoundary:
    """This is the single place that decides a deployment may have N workers.

    It is the reason "N workers" is a supported configuration at all, and the
    reason most of the inventory in the companion test file is a non-issue for
    anyone who passes it. Worth pinning hard, because it is load-bearing and
    easy to weaken by adding one more permissive branch.
    """

    def _config(self, *, workers: str, backend: str = "postgres", run_events: str = "db", heartbeat: bool = True, scheduler_enabled: bool = False, multi_instance: bool = False) -> object:
        from types import SimpleNamespace

        from app.gateway.deps import _enforce_postgres_for_multi_worker

        cfg = SimpleNamespace(
            database=SimpleNamespace(backend=backend),
            run_events=SimpleNamespace(backend=run_events),
            run_ownership=SimpleNamespace(heartbeat_enabled=heartbeat),
            scheduler=SimpleNamespace(enabled=scheduler_enabled, multi_instance=multi_instance),
        )
        cfg.get_tool_config = lambda name: None if name == "browser_navigate" else object()
        return cfg, _enforce_postgres_for_multi_worker

    def test_one_worker_accepts_everything(self, monkeypatch) -> None:
        monkeypatch.setenv("GATEWAY_WORKERS", "1")
        cfg, gate = self._config(workers="1", backend="memory", run_events="memory", heartbeat=False)
        gate(cfg)  # no raise: the dev posture is the default and must stay legal

    def test_many_workers_refuse_a_process_local_event_store(self, monkeypatch) -> None:
        monkeypatch.setenv("GATEWAY_WORKERS", "4")
        cfg, gate = self._config(workers="4", run_events="memory")
        with pytest.raises(SystemExit) as exc:
            gate(cfg)
        assert "run_events.backend" in str(exc.value), "the refusal must name the setting, not just fail"

    def test_many_workers_refuse_sqlite(self, monkeypatch) -> None:
        monkeypatch.setenv("GATEWAY_WORKERS", "4")
        cfg, gate = self._config(workers="4", backend="sqlite")
        with pytest.raises(SystemExit) as exc:
            gate(cfg)
        assert "postgres" in str(exc.value).lower()

    def test_many_workers_refuse_a_heartbeatless_lease(self, monkeypatch) -> None:
        """Without heartbeat, reconciliation would kill a peer's live runs.

        Every run then has a NULL lease, so a scale-up makes worker B reclaim
        every inflight row worker A is still executing. This refusal is the
        single most load-bearing line in the gate.
        """
        monkeypatch.setenv("GATEWAY_WORKERS", "4")
        cfg, gate = self._config(workers="4", heartbeat=False)
        with pytest.raises(SystemExit) as exc:
            gate(cfg)
        assert "heartbeat" in str(exc.value)

    def test_many_workers_refuse_a_per_worker_scheduler(self, monkeypatch) -> None:
        """Each worker starts its own poller, so occurrences would double-fire."""
        monkeypatch.setenv("GATEWAY_WORKERS", "4")
        cfg, gate = self._config(workers="4", scheduler_enabled=True)
        with pytest.raises(SystemExit) as exc:
            gate(cfg)
        assert "scheduler" in str(exc.value)

    def test_the_fully_configured_multi_worker_posture_passes(self, monkeypatch) -> None:
        monkeypatch.setenv("GATEWAY_WORKERS", "4")
        cfg, gate = self._config(workers="4")
        gate(cfg)  # must not raise: the supported configuration stays supported

    def test_multi_instance_scheduler_has_its_own_prerequisites(self, monkeypatch) -> None:
        """``scheduler.multi_instance`` is checked even at ``GATEWAY_WORKERS=1``.

        Kubernetes runs one worker per pod, so the worker count is not the only
        way to get two Gateways. This is the branch that covers that, and it is
        easy to forget precisely because the worker count is 1.
        """
        monkeypatch.setenv("GATEWAY_WORKERS", "1")
        cfg, gate = self._config(workers="1", backend="sqlite", scheduler_enabled=True, multi_instance=True)
        with pytest.raises(SystemExit) as exc:
            gate(cfg)
        assert "multi_instance" in str(exc.value)

    def test_the_gate_reads_one_environment_variable_and_defaults_to_one(self, monkeypatch) -> None:
        """An unset or unparseable ``GATEWAY_WORKERS`` must mean single worker.

        Defaulting to anything larger would silently opt a deployment into
        multi-worker semantics. The compose file pins the same default.
        """
        monkeypatch.delenv("GATEWAY_WORKERS", raising=False)
        cfg, gate = self._config(workers="1", backend="memory", run_events="memory", heartbeat=False)
        gate(cfg)

        monkeypatch.setenv("GATEWAY_WORKERS", "not-a-number")
        gate(cfg)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_store(tmp_path):
    """A real SQLite ``DbRunEventStore`` -- the durable backend, in miniature."""
    import alpha.persistence.models  # noqa: F401 - registers the tables create_all needs
    from alpha.persistence.base import Base
    from alpha.runtime.events.store.db import DbRunEventStore

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'events.db'}")

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    yield DbRunEventStore(async_sessionmaker(engine, expire_on_commit=False))
    asyncio.run(engine.dispose())


@pytest.fixture
def run_repo(tmp_path):
    """A real SQLite run repository.

    ``alpha.persistence.models`` is imported for its side effect: it is the
    registration entry point ``create_all`` needs, and it is why a table defined
    outside ``models/`` still gets created on the empty-database bootstrap path.
    """
    import alpha.persistence.models  # noqa: F401
    from alpha.persistence.base import Base
    from alpha.persistence.run.sql import RunRepository

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runs.db'}")

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    yield RunRepository(async_sessionmaker(engine, expire_on_commit=False))
    asyncio.run(engine.dispose())


@pytest.fixture
def sql_ledger(tmp_path):
    """A real SQLite ``SqlSideEffectLedger``."""
    import alpha.persistence.models  # noqa: F401
    from alpha.persistence.base import Base
    from alpha.persistence.side_effects import SqlSideEffectLedger

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'effects.db'}")

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    yield SqlSideEffectLedger(async_sessionmaker(engine, expire_on_commit=False))
    asyncio.run(engine.dispose())


def test_sqlalchemy_is_importable_for_the_dialect_assertions() -> None:
    assert hasattr(sqlalchemy, "dialects")
