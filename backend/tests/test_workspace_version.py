"""Tests for WorkspaceVersionStore — optimistic concurrency control for subagent workspaces."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from alpha.subagents.workspace_version import (
    ConflictError,
    WorkspaceVersionStore,
    validate_and_commit_writes,
)


class TestVersionCounter:
    """Tests for the per-workspace version counter."""

    def test_initial_version_is_zero(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        assert store.get_version() == 0

    def test_increment_returns_new_version(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        v1 = store.increment()
        assert v1 == 1
        v2 = store.increment()
        assert v2 == 2

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        store1 = WorkspaceVersionStore(tmp_path)
        store1.increment()
        store1.increment()
        store2 = WorkspaceVersionStore(tmp_path)
        assert store2.get_version() == 2

    def test_concurrent_increments_are_serialized(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(50):
                    store.increment()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert store.get_version() == 200

    def test_version_file_is_json(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        store.increment()
        version_file = tmp_path / ".workspace_version.json"
        assert version_file.exists()
        data = json.loads(version_file.read_text())
        assert data["version"] == 1


class TestValidateAndCommitWrites:
    """Tests for validate_and_commit_writes conflict detection."""

    def test_no_conflict_single_writer(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        store.increment()  # version 1
        # Writer read at version 1, writes, commits
        result = validate_and_commit_writes(
            store,
            read_version=1,
            written_paths=["file1.py", "file2.py"],
        )
        assert result is True
        assert store.get_version() == 2

    def test_conflict_when_version_changed(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        store.increment()  # version 1
        store.increment()  # version 2 (another writer)
        # Writer read at version 1, but version is now 2
        with pytest.raises(ConflictError):
            validate_and_commit_writes(
                store,
                read_version=1,
                written_paths=["file1.py"],
            )

    def test_conflict_includes_details(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        store.increment()
        store.increment()
        store.increment()  # version 3
        with pytest.raises(ConflictError) as exc_info:
            validate_and_commit_writes(
                store,
                read_version=1,
                written_paths=["file1.py"],
            )
        err = exc_info.value
        assert err.read_version == 1
        assert err.current_version == 3
        assert "file1.py" in err.conflicting_paths

    def test_no_write_paths_always_succeeds(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        store.increment()
        # No write paths = nothing to conflict
        result = validate_and_commit_writes(
            store,
            read_version=1,
            written_paths=[],
        )
        assert result is True

    def test_commit_with_empty_store_succeeds(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        result = validate_and_commit_writes(
            store,
            read_version=0,
            written_paths=["new_file.py"],
        )
        assert result is True
        assert store.get_version() == 1


class TestReadWriteTracking:
    """Tests for tracking read/write sets per subagent."""

    def test_record_read_adds_to_read_set(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        store.record_read("subagent-1", "file.py")
        reads = store.get_read_set("subagent-1")
        assert "file.py" in reads

    def test_record_write_adds_to_write_set(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        store.record_write("subagent-1", "file.py")
        writes = store.get_write_set("subagent-1")
        assert "file.py" in writes

    def test_clear_subagent_tracks_only_target(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        store.record_read("subagent-1", "file1.py")
        store.record_write("subagent-1", "file2.py")
        store.record_read("subagent-2", "file3.py")
        store.record_write("subagent-2", "file4.py")
        store.clear_subagent("subagent-1")
        assert store.get_read_set("subagent-1") == set()
        assert store.get_write_set("subagent-1") == set()
        # subagent-2 unaffected
        assert "file3.py" in store.get_read_set("subagent-2")
        assert "file4.py" in store.get_write_set("subagent-2")

    def test_get_base_version_for_subagent(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        store.increment()  # version 1
        store.record_read("subagent-1", "file.py")
        store.record_write("subagent-1", "file2.py")
        # Base version is the version when tracking started
        assert store.get_base_version("subagent-1") == 1

    def test_multiple_reads_same_file_no_duplicates(self, tmp_path: Path) -> None:
        store = WorkspaceVersionStore(tmp_path)
        store.record_read("subagent-1", "file.py")
        store.record_read("subagent-1", "file.py")
        reads = store.get_read_set("subagent-1")
        assert len(reads) == 1


class TestIntegrationPatterns:
    """Integration-style tests mimicking SubagentExecutor usage."""

    def test_two_subagents_sequential_no_conflict(self, tmp_path: Path) -> None:
        """Two subagents writing sequentially should not conflict."""
        store = WorkspaceVersionStore(tmp_path)

        # Subagent 1 reads and writes
        store.record_read("agent-1", "shared.py")
        store.record_write("agent-1", "shared.py")
        base_v1 = store.get_base_version("agent-1")
        assert validate_and_commit_writes(store, base_v1, ["shared.py"])

        # Subagent 2 reads the updated version and writes
        store.record_read("agent-2", "shared.py")
        store.record_write("agent-2", "shared.py")
        base_v2 = store.get_base_version("agent-2")
        assert validate_and_commit_writes(store, base_v2, ["shared.py"])

    def test_two_subagents_concurrent_conflict_detected(self, tmp_path: Path) -> None:
        """Two subagents reading the same version then both writing should conflict."""
        store = WorkspaceVersionStore(tmp_path)
        store.increment()  # version 1

        # Both read at version 1
        store.record_read("agent-1", "shared.py")
        store.record_read("agent-2", "shared.py")
        base_v1 = store.get_base_version("agent-1")
        base_v2 = store.get_base_version("agent-2")

        # First commits successfully
        assert validate_and_commit_writes(store, base_v1, ["shared.py"])

        # Second should conflict
        with pytest.raises(ConflictError):
            validate_and_commit_writes(store, base_v2, ["shared.py"])

    def test_subagent_lifecycle_read_write_clear(self, tmp_path: Path) -> None:
        """Full lifecycle: track, commit, clear."""
        store = WorkspaceVersionStore(tmp_path)

        # Phase 1: agent reads files
        store.record_read("agent-1", "config.py")
        store.record_read("agent-1", "main.py")

        # Phase 2: agent writes files
        store.record_write("agent-1", "output.py")
        store.record_write("agent-1", "main.py")

        # Phase 3: commit
        base_v = store.get_base_version("agent-1")
        written = list(store.get_write_set("agent-1"))
        assert validate_and_commit_writes(store, base_v, written)

        # Phase 4: cleanup
        store.clear_subagent("agent-1")
        assert store.get_read_set("agent-1") == set()
        assert store.get_write_set("agent-1") == set()
