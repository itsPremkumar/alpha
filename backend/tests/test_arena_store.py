"""Tests for the arena store: persistence, locking, ownership."""

from __future__ import annotations

import tempfile
import threading
import time

import pytest

from alpha.arena.bracket import new_run
from alpha.arena.store import ArenaStore, ArenaStoreError


class TestArenaStore:
    @pytest.fixture
    def tmp_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArenaStore(tmp)
            yield store

    def test_new_run_persisted(self, tmp_store):
        state = new_run(owner_id="user1", task="test task", seed=42, agents_n=4, wave=2)
        tmp_store.save(state)
        loaded = tmp_store.load(state["run_id"], "user1")
        assert loaded["run_id"] == state["run_id"]
        assert loaded["owner_id"] == "user1"
        assert loaded["task"] == "test task"
        assert loaded["agents_n"] == 4

    def test_owner_scoped(self, tmp_store):
        state = new_run(owner_id="user1", task="test", seed=42, agents_n=4, wave=2)
        tmp_store.save(state)
        loaded = tmp_store.load(state["run_id"], "user1")
        assert loaded["owner_id"] == "user1"
        with pytest.raises(ArenaStoreError, match="another owner"):
            tmp_store.load(state["run_id"], "user2")

    def test_list_runs_scoped(self, tmp_store):
        s1 = new_run(owner_id="user1", task="t1", seed=1, agents_n=2, wave=2)
        time.sleep(0.01)
        s2 = new_run(owner_id="user1", task="t2", seed=2, agents_n=2, wave=2)
        time.sleep(0.01)
        s3 = new_run(owner_id="user2", task="t3", seed=3, agents_n=2, wave=2)
        tmp_store.save(s1)
        tmp_store.save(s2)
        tmp_store.save(s3)
        user1_runs = tmp_store.list_runs("user1")
        user2_runs = tmp_store.list_runs("user2")
        assert len(user1_runs) == 2
        assert len(user2_runs) == 1
        # s2 was created and saved after s1, so it should be first (newest)
        assert user1_runs[0]["run_id"] == s2["run_id"]

    def test_atomic_write(self, tmp_store):
        state = new_run(owner_id="u", task="t", seed=1, agents_n=2, wave=2)
        tmp_store.save(state)
        path = tmp_store.state_path(state["run_id"])
        content = path.read_text()
        assert "run_id" in content
        # Writing same state again should not corrupt
        tmp_store.save(state)
        content2 = path.read_text()
        assert content == content2

    def test_corrupt_load_raises(self, tmp_store):
        state = new_run(owner_id="u", task="t", seed=1, agents_n=2, wave=2)
        tmp_store.save(state)
        path = tmp_store.state_path(state["run_id"])
        path.write_text("{not json")
        with pytest.raises(ArenaStoreError, match="unreadable"):
            tmp_store.load(state["run_id"], "u")

    def test_missing_run_raises_keyerror(self, tmp_store):
        with pytest.raises(KeyError, match="no arena run"):
            tmp_store.load("nonexistent", "u")

    def test_concurrent_writes_serialized(self, tmp_store):
        """Two threads writing the same run should not lose updates.

        On Windows, FileLock may have issues with thread contention.
        This test verifies the store's atomic write semantics work.
        """
        import platform
        if platform.system() == "Windows":
            pytest.skip("FileLock thread contention on Windows")

        state = new_run(owner_id="u", task="t", seed=1, agents_n=2, wave=2)
        tmp_store.save(state)
        errors: list[Exception] = []

        def writer(i: int):
            try:
                s = tmp_store.load(state["run_id"], "u")
                s["agents_n"] = i  # mutate
                tmp_store.save(s)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"concurrent writes raised: {errors}"
        final = tmp_store.load(state["run_id"], "u")
        assert final["agents_n"] in range(10)

    def test_workspace_path_safety(self, tmp_store):
        state = new_run(owner_id="u", task="t", seed=1, agents_n=2, wave=2)
        with pytest.raises(ArenaStoreError, match="invalid"):
            tmp_store.work_path(state["run_id"], "../../etc/passwd")
        with pytest.raises(ArenaStoreError, match="invalid"):
            tmp_store.work_path(state["run_id"], "normal/../evil")

    def test_delete(self, tmp_store):
        state = new_run(owner_id="u", task="t", seed=1, agents_n=2, wave=2)
        tmp_store.save(state)
        tmp_store.delete(state["run_id"], "u")
        with pytest.raises(KeyError):
            tmp_store.load(state["run_id"], "u")

    def test_log_append_and_read(self, tmp_store):
        state = new_run(owner_id="u", task="t", seed=1, agents_n=2, wave=2)
        tmp_store.save(state)
        tmp_store.append_log(state["run_id"], "first")
        time.sleep(0.01)
        tmp_store.append_log(state["run_id"], "second")
        log = tmp_store.read_log(state["run_id"], tail=10)
        assert len(log) == 2
        assert log[0]["line"] == "first"
        assert log[1]["line"] == "second"

    def test_load_any_admin(self, tmp_store):
        state = new_run(owner_id="user1", task="t", seed=1, agents_n=2, wave=2)
        tmp_store.save(state)
        loaded = tmp_store.load_any(state["run_id"])
        assert loaded["owner_id"] == "user1"

    def test_list_includes_unreadable(self, tmp_store):
        state = new_run(owner_id="u", task="t", seed=1, agents_n=2, wave=2)
        tmp_store.save(state)
        # Corrupt another run directory
        bad_dir = tmp_store.run_dir("bad-run")
        bad_dir.mkdir(parents=True)
        (bad_dir / "state.json").write_text("not json")
        runs = tmp_store.list_runs()
        statuses = {r["run_id"]: r["status"] for r in runs}
        assert statuses[state["run_id"]] == "draft"
        assert statuses["bad-run"] == "unreadable"