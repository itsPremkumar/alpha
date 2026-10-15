"""CodingWorktreeWorker must APPLY, COMMIT, and VERIFY — not just write a patch file.

The worker used to create a worktree, drop ``<task>.patch`` into it, and report
``success``. Nothing ever ran ``git apply``, nothing was committed, and no merge
was ever attempted, so a "completed" coding task left a branch with zero
commits and a diff nobody had checked git could even take. Every assertion
below has teeth against that exact behaviour: the change must be visible in the
worktree's files, on the branch's history, and — when asked — judged by a real
merge simulation whose outcome is reported, never assumed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import alpha.swarm.runner as runner_mod
import alpha.swarm.worker as worker_mod
from alpha.sandbox.merge_simulation import MergeOutcome, MergeSimulation
from alpha.sandbox.worktree_strategy import Assurance, WorktreeMode, WorktreeSelection
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode
from alpha.swarm.runner import AsyncSwarmRunner
from alpha.swarm.worker import CodingWorktreeWorker


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real one-commit repository, so apply/commit/merge run against real git."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Swarm Test")
    git(root, "config", "user.email", "swarm@example.invalid")
    (root / "app.py").write_text("old\n", encoding="utf-8", newline="\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "baseline")
    return root


class _Message:
    def __init__(self, content):
        self.content = content


class _FakeModel:
    model_name = "stub-swarm-model"

    def __init__(self, content):
        self.content = content
        self.seen: list = []

    def invoke(self, messages):
        self.seen.append(messages)
        return _Message(self.content)


APPLIES_CLEANLY = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
APPLIES_TO_NOTHING = "--- a/absent.py\n+++ b/absent.py\n@@ -1 +1 @@\n-old\n+new\n"


def _plan() -> SwarmPlan:
    return SwarmPlan(swarm_id="swm-wt-worker", goal="Ship the feature", mode=SwarmMode.PARALLEL)


def _task(task_id: str = "task-wt-1") -> SwarmTaskNode:
    return SwarmTaskNode(task_id=task_id, objective="Flip app.py from old to new")


def test_swarm_task_branch_names_do_not_collide_after_sanitization() -> None:
    colon_id = runner_mod._worktree_branch_name("task:a")
    hyphen_id = runner_mod._worktree_branch_name("task-a")

    assert colon_id != hyphen_id
    assert len(colon_id) <= 200
    assert len(hyphen_id) <= 200
    assert all(character.isalnum() or character in "._-" for character in colon_id)


def _worker(repo: Path, wt_dir: Path, monkeypatch, diff: str, **kwargs) -> CodingWorktreeWorker:
    monkeypatch.setattr(
        worker_mod,
        "_resolve_model",
        lambda model_name, *, worker_label: _FakeModel(diff),
    )
    return CodingWorktreeWorker(repo, branch_name=kwargs.pop("branch_name", "wt-task-wt-1"), base_worktree_dir=wt_dir, **kwargs)


# ── the change must actually land in the worktree ───────────────────────────


def test_applies_diff_and_commits_on_the_branch(repo, tmp_path, monkeypatch):
    worker = _worker(repo, tmp_path / "wts", monkeypatch, APPLIES_CLEANLY, auto_commit=True)

    outcome = worker.execute_task(_task(), _plan())

    wt = Path(outcome["worktree_path"])
    # Teeth: the old worker never touched the file; the diff sat in a .patch
    # nobody applied.
    assert (wt / "app.py").read_text(encoding="utf-8") == "new\n"
    # Teeth: the authored patch file is an artifact, not part of the change —
    # it must not be committed onto the branch.
    assert not (wt / "task-wt-1.patch").exists()
    assert git(wt, "status", "--porcelain").stdout.strip() == ""
    log = git(wt, "log", "--format=%s", "HEAD~1..HEAD").stdout.strip()
    assert log.endswith("on wt-task-wt-1")
    head = git(wt, "rev-parse", "HEAD").stdout.strip()

    evidence = outcome["evidence"][0]
    assert evidence["applied"] is True
    assert evidence["committed"] is True
    assert evidence["commit_sha"] == head
    assert "applied via git apply" in outcome["summary"]
    assert f"committed {head[:12]}" in outcome["summary"]
    assert outcome["result_payload"]["commit_sha"] == head
    assert outcome["status"] == "success"


def test_apply_failure_is_an_honest_failure_not_a_success(repo, tmp_path, monkeypatch):
    worker = _worker(repo, tmp_path / "wts", monkeypatch, APPLIES_TO_NOTHING, auto_commit=True)

    with pytest.raises(RuntimeError, match="git apply failed"):
        worker.execute_task(_task(), _plan())

    # Teeth: nothing may be committed for a diff that did not apply.
    branch_commits = git(repo, "log", "--format=%s", "wt-task-wt-1").stdout.strip().splitlines()
    assert branch_commits == ["baseline"]


def test_uncommitted_changes_are_left_in_place_when_auto_commit_is_off(repo, tmp_path, monkeypatch):
    worker = _worker(repo, tmp_path / "wts", monkeypatch, APPLIES_CLEANLY, auto_commit=False)

    outcome = worker.execute_task(_task(), _plan())

    wt = Path(outcome["worktree_path"])
    assert (wt / "app.py").read_text(encoding="utf-8") == "new\n"
    # The applied change is real but uncommitted — recoverable, and said so.
    assert " M app.py" in git(wt, "status", "--porcelain").stdout
    evidence = outcome["evidence"][0]
    assert evidence["applied"] is True
    assert evidence["committed"] is False
    assert evidence.get("commit_sha") is None
    assert "committed" not in outcome["summary"]


# ── merge simulation is run and reported, never assumed ─────────────────────


def test_simulate_merge_records_the_measured_outcome(repo, tmp_path, monkeypatch):
    worker = _worker(repo, tmp_path / "wts", monkeypatch, APPLIES_CLEANLY, auto_commit=True, simulate_merge=True)

    outcome = worker.execute_task(_task(), _plan())

    simulation = outcome["result_payload"]["merge_simulation"]
    assert simulation["outcome"] == "clean"
    assert simulation["is_mergeable"] is True
    # A clean merge is a textual fact, not a correctness verdict, and the
    # disclosure must say so rather than reporting "safe" or "verified".
    assert "NOT that the result is correct" in simulation["disclosure"]
    assert "verified" not in simulation
    assert "safe" not in simulation
    merge_evidence = next(item for item in outcome["evidence"] if item["source"] == "merge_simulation")
    assert merge_evidence["outcome"] == "clean"
    assert "merge simulation into HEAD: clean" in outcome["summary"]


def test_conflicting_diff_is_reported_as_conflicted(tmp_path, monkeypatch):
    """A measured conflict is relayed verbatim — never softened into "clean".

    A real conflict needs the base to move *between* the worktree being
    created and the simulation being judged, and ``WorktreeManager`` refuses to
    reuse a branch whose base has moved, so this state cannot be produced
    deterministically through one ``execute_task`` call. What is testable — and
    what matters — is that the worker reports whatever git measured, including
    the failure case, instead of substituting its own verdict.
    """

    class _ConflictManager:
        def __init__(self, repo_root=None, base_worktree_dir=None, **kwargs):
            pass

        def create_worktree(self, *, branch_name):
            target = tmp_path / "wt"
            target.mkdir(parents=True, exist_ok=True)
            return SimpleNamespace(path=target)

        def run_in_worktree(self, path, args, *, check=True):
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def simulate_merge(self, *, base_ref, head_ref, name):
            return MergeSimulation(
                outcome=MergeOutcome.CONFLICTED,
                base_ref=base_ref,
                base_sha="a" * 40,
                head_sha="b" * 40,
                reason="git stopped with unmerged paths",
                conflicted_files=("app.py",),
            )

    monkeypatch.setattr(worker_mod, "WorktreeManager", _ConflictManager)
    monkeypatch.setattr(worker_mod, "_resolve_model", lambda model_name, *, worker_label: _FakeModel(APPLIES_CLEANLY))

    worker = CodingWorktreeWorker(".", branch_name="wt-task-wt-1", auto_commit=True, simulate_merge=True)
    outcome = worker.execute_task(_task(), _plan())

    simulation = outcome["result_payload"]["merge_simulation"]
    assert simulation["outcome"] == "conflicted"
    assert simulation["is_mergeable"] is False
    assert simulation["conflicted_files"] == ["app.py"]
    assert "CONFLICTS" in simulation["disclosure"]
    # Teeth: a conflict must not be reported as a completed, mergeable change.
    assert "merge simulation into HEAD: conflicted" in outcome["summary"]
    merge_evidence = next(item for item in outcome["evidence"] if item["source"] == "merge_simulation")
    assert merge_evidence["is_mergeable"] is False
    assert merge_evidence["conflicted_files"] == ["app.py"]


# ── no side effects for a task that needs no edit ───────────────────────────


def test_no_changes_short_circuits_without_creating_a_worktree(monkeypatch):
    class _SpyManager:
        def __init__(self, repo_root=None, base_worktree_dir=None, **kwargs):
            self.base_worktree_dir = base_worktree_dir

        def create_worktree(self, *, branch_name):
            raise AssertionError("a NO_CHANGES task must not provision a worktree")

    monkeypatch.setattr(worker_mod, "WorktreeManager", _SpyManager)
    monkeypatch.setattr(worker_mod, "_resolve_model", lambda model_name, *, worker_label: _FakeModel("NO_CHANGES"))

    worker = CodingWorktreeWorker(".", branch_name="wt-task-wt-1")
    outcome = worker.execute_task(_task(), _plan())

    assert outcome["status"] == "success"
    assert "No file changes required" in outcome["summary"]
    assert "worktree_path" not in outcome
    assert outcome["evidence"][0]["changes_authored"] is False


def test_base_worktree_dir_is_honored(repo, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    worker = _worker(repo, elsewhere, monkeypatch, APPLIES_CLEANLY, auto_commit=True)

    outcome = worker.execute_task(_task(), _plan())

    assert Path(outcome["worktree_path"]).parent == elsewhere
    # Teeth: the default <repo>/.worktrees location must not be used.
    assert not (repo / ".worktrees").exists()


# ── the runner must choose the isolation, not assume it ─────────────────────


def _node(**kwargs) -> SwarmTaskNode:
    payload = {"task_id": "task-wt-1", "objective": "Flip app.py", "worktree_path": "descriptive-metadata"}
    payload.update(kwargs)
    return SwarmTaskNode(**payload)


def test_build_worker_wires_worktree_strategy_and_options(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    monkeypatch.setenv("ALPHA_PROJECT_ROOT", str(tmp_path))

    runner = AsyncSwarmRunner(SimpleNamespace())
    worker = runner._build_worker(_node())

    assert isinstance(worker, CodingWorktreeWorker)
    assert worker.branch_name == runner_mod._worktree_branch_name("task-wt-1")
    assert worker.auto_commit is True
    assert worker.simulate_merge is True
    # Swarm worktrees live in runtime state, not inside the repository tree.
    assert Path(worker.manager.worktrees_dir).is_relative_to(tmp_path)


def test_build_worker_refuses_when_worktree_isolation_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    degraded = WorktreeSelection(
        mode=WorktreeMode.COPY,
        assurance=Assurance.LOWER,
        reason="git exited 128: unable to access '.git'",
    )
    monkeypatch.setattr(runner_mod, "select_strategy", lambda signals: degraded)

    runner = AsyncSwarmRunner(SimpleNamespace())
    with pytest.raises(RuntimeError, match="worktree isolation is unavailable"):
        runner._build_worker(_node())
