"""Worktree resource limits: disk bounds and stale claim cleanup.

These tests pin the two resource-limit properties:
* ``provision_worktree`` refuses a worktree that exceeds a declared disk bound;
* ``cleanup_stale_claims`` reclaims CLAIMED records whose path no longer exists.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from alpha.workflow.worktrees import (
    WorktreeStore,
    disk_usage_bytes,
    provision_worktree,
)

# ---------------------------------------------------------- disk_usage_bytes


def test_disk_usage_bytes_measures_directory():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        (path / "file1.txt").write_text("hello")
        (path / "subdir").mkdir()
        (path / "subdir" / "file2.txt").write_text("world")
        usage = disk_usage_bytes(path)
        assert usage == len(b"hello") + len(b"world")


def test_disk_usage_bytes_returns_zero_for_missing_path():
    assert disk_usage_bytes(Path("/nonexistent/path/that/does/not/exist")) == 0


# --------------------------------------------------- provision disk bound


def test_provision_worktree_enforces_disk_bound():
    """A worktree that exceeds the disk bound is refused and cleaned up."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        # Initialize a git repo
        os.system(f'git -C "{repo}" init -q')
        os.system(f'git -C "{repo}" config user.email "test@test.com"')
        os.system(f'git -C "{repo}" config user.name "Test"')
        (repo / "file.txt").write_text("content")
        os.system(f'git -C "{repo}" add .')
        os.system(f'git -C "{repo}" commit -q -m "init"')

        worktree_path = root / "worktree"
        # A very small bound (1 byte) should refuse any real worktree
        outcome = provision_worktree(
            path=worktree_path,
            repo_root=str(repo),
            base_ref="HEAD",
            max_disk_usage_bytes=1,
        )
        assert not outcome.ok
        assert "exceeds bound" in outcome.reason


def test_provision_worktree_allows_under_bound():
    """A worktree under the disk bound is allowed."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        os.system(f'git -C "{repo}" init -q')
        os.system(f'git -C "{repo}" config user.email "test@test.com"')
        os.system(f'git -C "{repo}" config user.name "Test"')
        (repo / "file.txt").write_text("content")
        os.system(f'git -C "{repo}" add .')
        os.system(f'git -C "{repo}" commit -q -m "init"')

        worktree_path = root / "worktree"
        # A very large bound should allow the worktree
        outcome = provision_worktree(
            path=worktree_path,
            repo_root=str(repo),
            base_ref="HEAD",
            max_disk_usage_bytes=1_000_000_000,
        )
        assert outcome.ok
        assert outcome.head_commit


# --------------------------------------------------- cleanup_stale_claims


def test_cleanup_stale_claims_removes_missing_paths():
    with tempfile.TemporaryDirectory() as tmp:
        store = WorktreeStore()
        root = Path(tmp)

        # Create a claim for a path that exists
        existing_path = root / "existing"
        existing_path.mkdir()
        claim1 = store.claim(run_id="run_1", node_id="n1", path=existing_path, repo_root=str(root))

        # Create a claim for a path that does NOT exist
        missing_path = root / "missing"
        claim2 = store.claim(run_id="run_2", node_id="n2", path=missing_path, repo_root=str(root))

        removed = store.cleanup_stale_claims(root=root)
        assert claim2.claim_id in removed
        assert claim1.claim_id not in removed

        # The existing claim is still there
        assert store.get(claim1.claim_id) is not None
        # The stale claim is gone
        assert store.get(claim2.claim_id) is None


def test_cleanup_stale_claims_keeps_all_when_paths_exist():
    with tempfile.TemporaryDirectory() as tmp:
        store = WorktreeStore()
        root = Path(tmp)

        path1 = root / "p1"
        path1.mkdir()
        path2 = root / "p2"
        path2.mkdir()

        store.claim(run_id="run_1", node_id="n1", path=path1, repo_root=str(root))
        store.claim(run_id="run_2", node_id="n2", path=path2, repo_root=str(root))

        removed = store.cleanup_stale_claims(root=root)
        assert len(removed) == 0
