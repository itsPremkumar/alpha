"""Cross-process coordination for the lease store (research matrix item 4).

The lease store always wrote atomically (``os.replace``), which prevents torn
writes but not *lost updates*: two processes could read the same state, each
apply its own mutation, and the second replace silently discarded the first
process's change. This suite pins the coordination layer that closes it:

* ``FileLock`` is genuinely exclusive across instances (the in-process
  stand-in for across processes), times out with the real elapsed time, and
  locks a ``.lock`` sidecar — never the data file the store keeps replacing;
* two ``LeaseManager`` instances over one directory behave like two processes:
  every mutation reloads under the lock, so a lease, a reclaim or a forgotten
  run in one instance is visible to — and honoured by — the other;
* a fence another instance advanced is visible, so a late result from a
  superseded attempt reads ``stale_lease``, never ``accepted``;
* a lock that cannot be taken is a ``LeaseStoreError``, never a silent
  unlocked mutation;
* a process-local manager (no ``store_dir``) touches no filesystem at all;
* a corrupt, wrong-version or malformed store fails loudly at construction.

Honest boundary: these locks are **advisory** and filesystem-local. They stop
lost updates between cooperating processes on one machine. They are not
distributed locks, and the store remains restart-recoverable single-Gateway
state — never cross-process *exactly-once*, never cross-host.
"""

from __future__ import annotations

import json
import threading

import pytest

import alpha.workflow.leases as leases_mod
from alpha.utils.file_lock import FileLock, FileLockTimeout
from alpha.workflow.leases import LeaseManager, LeaseStoreError, ResultVerdict

WORKER_A = "gateway-pid-1"
WORKER_B = "gateway-pid-2"


def _shared(tmp_path) -> LeaseManager:
    """A manager over a store that does not exist yet (fresh process)."""
    return LeaseManager(tmp_path / "shared_store")


# ------------------------------------------------------------------- FileLock


def test_two_lock_instances_cannot_hold_the_same_sidecar(tmp_path):
    data = tmp_path / "store.json"
    holder = FileLock(data, timeout=1.0)
    waiter = FileLock(data, timeout=0.15)

    holder.acquire()
    try:
        with pytest.raises(FileLockTimeout) as excinfo:
            waiter.acquire()
        # The failure names the lock it wanted and who has it — not a silent
        # success that would let both "processes" mutate at once.
        assert "store.json.lock" in str(excinfo.value)
        assert "another process holds it" in str(excinfo.value)
    finally:
        holder.release()

    # Released means acquirable: the waiter's timeout was contention, not a
    # permanent failure.
    waiter.acquire()
    assert waiter.is_held
    waiter.release()


def test_the_lock_is_a_sidecar_never_the_data_file(tmp_path):
    """The data file is replaced atomically; locking it would invalidate the fd."""
    data = tmp_path / "leases.json"

    with FileLock(data) as lock:
        assert lock.is_held
        assert data.exists() is False, "the lock must not create or touch the store itself"
        assert (tmp_path / "leases.json.lock").exists() is True


def test_a_second_acquire_on_one_instance_is_a_loud_failure(tmp_path):
    lock = FileLock(tmp_path / "x.json")
    lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="already held"):
            lock.acquire()
    finally:
        lock.release()


def test_the_context_manager_releases_even_when_the_body_raises(tmp_path):
    data = tmp_path / "x.json"

    with pytest.raises(ValueError, match="boom"):
        with FileLock(data, timeout=1.0):
            raise ValueError("boom")

    other = FileLock(data, timeout=0.5)
    other.acquire()
    assert other.is_held
    other.release()


# ------------------------------------------------- two instances, one store


def test_a_second_manager_sees_a_lease_the_first_just_took(tmp_path):
    early = _shared(tmp_path)  # constructed BEFORE the lease existed on disk
    late = _shared(tmp_path)

    lease = late.acquire_lease("run_see", "n1", WORKER_A)
    assert lease is not None
    assert lease.fence_token == 1

    seen = early.get_lease("run_see", "n1")
    assert seen is not None, "a manager built on an empty store must reload, not answer from stale memory"
    assert seen.worker_id == WORKER_A
    assert seen.fence_token == 1


def test_a_manager_that_never_saw_the_lease_still_refuses_to_hand_it_out(tmp_path):
    early = _shared(tmp_path)
    late = _shared(tmp_path)
    late.acquire_lease("run_hand", "n1", WORKER_A)

    # Without the reload under the lock this would mint a second claim on an
    # empty in-memory map — two workers on one node.
    assert early.acquire_lease("run_hand", "n1", WORKER_B) is None

    # The refused attempt must not have advanced the fence either: worker A's
    # result still verifies.
    verdict = late.check_result("run_hand", "n1", worker_id=WORKER_A, fence_token=1)
    assert verdict is ResultVerdict.ACCEPTED


def test_a_fence_advanced_by_another_manager_fences_out_the_late_result(tmp_path):
    early = _shared(tmp_path)
    late = _shared(tmp_path)
    lease = late.acquire_lease("run_fence", "n1", WORKER_A)
    assert lease is not None and lease.fence_token == 1

    # Orphan recovery in a process that never held the lease: this only finds
    # it by reloading, and only fences it by persisting.
    superseded = early.reclaim("run_fence", "n1")
    assert superseded is not None
    assert superseded.worker_id == WORKER_A

    # Worker A's result lands afterwards — the exact case the fence exists for.
    verdict = late.check_result("run_fence", "n1", worker_id=WORKER_A, fence_token=1)
    assert verdict is ResultVerdict.STALE_LEASE


def test_forgetting_a_run_in_one_manager_makes_its_results_unknown_in_the_other(tmp_path):
    early = _shared(tmp_path)
    late = _shared(tmp_path)
    late.acquire_lease("run_forget", "n1", WORKER_A)

    assert early.forget_run("run_forget") == 1

    # Dropped fences are refused, not accepted: an unverifiable attempt never
    # writes through just because the key is gone.
    verdict = late.check_result("run_forget", "n1", worker_id=WORKER_A, fence_token=1)
    assert verdict is ResultVerdict.UNKNOWN_LEASE


def test_the_fence_keeps_climbing_across_instances(tmp_path):
    first = _shared(tmp_path)
    first.acquire_lease("run_climb", "n1", WORKER_A)
    first.release_lease("run_climb", "n1", WORKER_A)

    second = _shared(tmp_path)  # a fresh process reading the same store
    lease = second.acquire_lease("run_climb", "n1", WORKER_B)

    assert lease is not None
    assert lease.fence_token == 2, "release keeps the counter; a fresh instance must not restart it at 1"


def test_concurrent_instances_produce_exactly_one_winner(tmp_path):
    """The read-modify-write is atomic across instances, so nobody ties."""
    managers = [_shared(tmp_path), _shared(tmp_path)]
    barrier = threading.Barrier(2)
    results: list = [None, None]

    def claim(index: int) -> None:
        barrier.wait(timeout=10)  # both instances hit the store together
        results[index] = managers[index].acquire_lease("run_race", "n1", f"worker-{index}")

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    winners = [lease for lease in results if lease is not None]
    assert len(winners) == 1, f"exactly one instance may hold the node, got {results!r}"


# --------------------------------------------------------- fail-closed edges


def test_a_lock_that_cannot_be_taken_is_a_store_error_not_an_unlocked_mutation(tmp_path, monkeypatch):
    store_dir = tmp_path / "held"
    store_dir.mkdir()
    manager = LeaseManager(store_dir)  # constructed before the store is contended

    holder = FileLock(store_dir / "leases.json", timeout=0.0)
    holder.acquire()
    try:
        # Shorten the wait instead of sitting out the production 5s bound.
        monkeypatch.setattr(
            leases_mod,
            "FileLock",
            lambda path, timeout=5.0: FileLock(path, timeout=0.1, poll_interval=0.01),
        )
        with pytest.raises(LeaseStoreError, match="cross-process lease lock unavailable"):
            manager.acquire_lease("run_lock", "n1", WORKER_A)
    finally:
        holder.release()

    # The mutation never landed: refusing to lock must not mutate unlocked.
    assert manager.get_lease("run_lock", "n1") is None


def test_a_process_local_manager_touches_no_filesystem(tmp_path):
    manager = LeaseManager()

    assert manager.store_path is None
    assert manager._cross_process_lock is None

    lease = manager.acquire_lease("run_local", "n1", WORKER_A)
    assert lease is not None
    assert manager.get_lease("run_local", "n1") is not None
    assert list(tmp_path.iterdir()) == [], "a dry run must not write a claim anywhere"


def test_a_corrupt_store_fails_loudly_at_construction(tmp_path):
    store_dir = tmp_path / "corrupt"
    store_dir.mkdir()
    (store_dir / "leases.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(LeaseStoreError, match="not valid JSON"):
        LeaseManager(store_dir)


def test_an_unsupported_schema_version_is_refused_not_reinterpreted(tmp_path):
    store_dir = tmp_path / "schema"
    store_dir.mkdir()
    payload = {"schema_version": 99, "leases": {}, "fences": {}}
    (store_dir / "leases.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LeaseStoreError, match="schema_version"):
        LeaseManager(store_dir)


def test_a_malformed_lease_record_is_refused_not_skipped(tmp_path):
    store_dir = tmp_path / "malformed"
    store_dir.mkdir()
    payload = {
        "schema_version": 1,
        "leases": {"run_m:n1": {"node_id": "n1", "run_id": "run_m", "worker_id": "w", "ttl_seconds": "soon"}},
        "fences": {},
    }
    (store_dir / "leases.json").write_text(json.dumps(payload), encoding="utf-8")

    # Dropping the record would silently resurrect a lease the store claimed
    # to hold, so the load fails instead.
    with pytest.raises(LeaseStoreError, match="malformed lease"):
        LeaseManager(store_dir)
