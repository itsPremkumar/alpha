"""Project resource locks: ownership, conflicts, requests, expiry."""

from __future__ import annotations

import pytest

from alpha.projects.locks import LockConflictError, LockManager


@pytest.fixture()
def manager(tmp_path):
    return LockManager(storage_path=tmp_path / "locks.json")


def test_acquire_and_reacquire_by_owner(manager):
    first = manager.acquire("p", "file", "auth.py", "coder", reason="implementing auth")
    second = manager.acquire("p", "file", "auth.py", "coder")
    assert first.lock_id == second.lock_id


def test_second_owner_gets_conflict_with_holder(manager):
    manager.acquire("p", "file", "auth.py", "coder", reason="implementing auth")
    with pytest.raises(LockConflictError) as exc_info:
        manager.acquire("p", "file", "auth.py", "frontend")
    assert exc_info.value.holder.owner_bot == "coder"


def test_dir_lock_covers_children(manager):
    manager.acquire("p", "dir", "backend/auth", "coder")
    with pytest.raises(LockConflictError):
        manager.acquire("p", "file", "backend/auth/service.py", "frontend")


def test_release_by_owner_and_supervisor_override(manager):
    lk = manager.acquire("p", "file", "a.py", "coder")
    assert manager.release(lk.lock_id, "frontend") is False
    assert manager.release(lk.lock_id, "supervisor") is True
    assert manager.release(lk.lock_id, "coder") is False


def test_request_and_resolve_flow(manager):
    lk = manager.acquire("p", "file", "a.py", "coder")
    rq = manager.request_access("p", "file", "a.py", "reviewer", mode="transfer")
    assert rq.status == "pending"
    resolved = manager.resolve_request(rq.request_id, "reviewer", approve=True)
    assert resolved is None
    resolved = manager.resolve_request(rq.request_id, "coder", approve=True)
    assert resolved is not None and resolved.status == "approved"
    assert manager._locks[lk.lock_id].owner_bot == "reviewer"


def test_request_on_unlocked_resource_fails(manager):
    with pytest.raises(ValueError):
        manager.request_access("p", "file", "free.py", "reviewer")


def test_expired_locks_sweep_and_reacquire(manager):
    lk = manager.acquire("p", "file", "a.py", "coder", ttl_seconds=60.0)
    lk.expires_at = 0.0
    assert manager.sweep_expired() == 1
    fresh = manager.acquire("p", "file", "a.py", "frontend")
    assert fresh.owner_bot == "frontend"


def test_locks_are_project_scoped(manager):
    manager.acquire("p1", "file", "a.py", "coder")
    manager.acquire("p2", "file", "a.py", "frontend")
    assert len(manager.list_locks("p1")) == 1
    assert len(manager.list_locks("p2")) == 1
