"""Per-task worktree claiming: exclusive, confined, and honestly reported.

Properties pinned here:

* the path is confined and sanitized — a node id carrying path syntax cannot
  escape the engine's worktree root;
* a claim is EXCLUSIVE: a second task asking for the same path is refused
  with the holder named, never silently sharing the checkout;
* provisioning and removal are real, bounded, argv-only git calls, and a
  failed removal leaves the claim ACTIVE — a worktree reported as gone while
  it still occupies disk would hand the same directory to the next task;
* the repo-root allowlist is host-bound: with nothing bound, a worktree node
  fails honestly instead of checking out whatever repository a request named.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from alpha.workflow.models import NodeType, WorkflowDefinition, WorkflowEdge, WorkflowGraph, WorkflowNode
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.worktrees import (
    WorktreeStatus,
    WorktreeStore,
    WorktreeStoreError,
    confined_worktree_path,
    provision_worktree,
    release_worktree,
    sanitized_repo_root,
)

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True, check=False).returncode != 0,
    reason="git is not available on this host",
)


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(cwd / ".gitconfig"),
        "GIT_AUTHOR_NAME": "alpha test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "alpha test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    completed = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env)
    return completed.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "--quiet")
    (root / "file.txt").write_text("hello", encoding="utf-8")
    _git(root, "add", "file.txt")
    _git(root, "commit", "--quiet", "-m", "init")
    return root


# ----------------------------------------------------------------------- policy


def test_confined_path_sanitizes_path_syntax(tmp_path: Path):
    path = confined_worktree_path(tmp_path, "run_1", "../../etc/passwd")
    # Confinement is the property: the result is a single flat component under
    # the declared root, so path syntax cannot walk out of it.
    assert path.parent == tmp_path
    assert ".." not in path.name
    assert "/" not in path.name and "\\" not in path.name
    other = confined_worktree_path(tmp_path, "run_1", "a/b c")
    assert other.parent == tmp_path
    assert " " not in other.name


def test_repo_root_normalization_is_comparison_only():
    assert sanitized_repo_root("C:\\Repo") == sanitized_repo_root("c:/repo")


def test_store_refuses_a_double_claim_and_names_the_holder(tmp_path: Path):
    store = WorktreeStore(tmp_path / "store")
    first = store.claim(run_id="r1", node_id="n1", path=Path("/tmp/wt/a"), repo_root="/tmp/repo")
    with pytest.raises(WorktreeStoreError, match="already claimed by run 'r1' node 'n1'"):
        store.claim(run_id="r2", node_id="n1", path=Path("/tmp/wt/a"), repo_root="/tmp/repo")
    # A different path is a different claim.
    store.claim(run_id="r2", node_id="n2", path=Path("/tmp/wt/b"), repo_root="/tmp/repo")
    assert first.status is WorktreeStatus.CLAIMED


def test_a_failed_release_keeps_the_claim_active(tmp_path: Path):
    store = WorktreeStore(tmp_path / "store")
    claim = store.claim(run_id="r1", node_id="n1", path=Path("/tmp/wt/a"), repo_root="/tmp/repo")
    store.mark_release_failed(claim.claim_id, reason="worktree is locked")
    held = store.get(claim.claim_id)
    assert held.status is WorktreeStatus.CLAIMED
    assert held.reason == "worktree is locked"
    # The exclusion still holds: nobody else may take the path while the
    # worktree physically occupies it.
    with pytest.raises(WorktreeStoreError):
        store.claim(run_id="r2", node_id="n1", path=Path("/tmp/wt/a"), repo_root="/tmp/repo")


def test_store_reloads_and_forgets_runs(tmp_path: Path):
    store = WorktreeStore(tmp_path / "store")
    store.claim(run_id="r1", node_id="n1", path=Path("/tmp/wt/a"), repo_root="/tmp/repo")
    store.claim(run_id="r2", node_id="n1", path=Path("/tmp/wt/b"), repo_root="/tmp/repo")
    reloaded = WorktreeStore(tmp_path / "store")
    assert len(reloaded.list()) == 2
    assert reloaded.forget_run("r1") == 1
    assert [claim.run_id for claim in reloaded.list()] == ["r2"]


def test_corrupt_store_fails_closed(tmp_path: Path):
    root = tmp_path / "store"
    root.mkdir(parents=True)
    (root / "worktrees.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(WorktreeStoreError, match="not valid JSON"):
        WorktreeStore(root)


# ------------------------------------------------------------------ real git


def test_provision_records_the_head_commit(repo: Path, tmp_path: Path):
    target = tmp_path / "wt" / "run__n1"
    outcome = provision_worktree(path=target, repo_root=str(repo), base_ref="HEAD")
    assert outcome.ok, outcome.reason
    assert outcome.head_commit
    head = _git(repo, "rev-parse", "HEAD")
    assert outcome.head_commit == head
    assert (target / "file.txt").read_text(encoding="utf-8") == "hello"


def test_provision_refuses_an_unknown_base_ref(repo: Path, tmp_path: Path):
    outcome = provision_worktree(path=tmp_path / "wt" / "run__n1", repo_root=str(repo), base_ref="refs/heads/nope")
    assert outcome.ok is False
    assert "worktree creation failed" in outcome.reason


def test_provision_refuses_a_non_git_directory(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    outcome = provision_worktree(path=tmp_path / "wt" / "run__n1", repo_root=str(plain), base_ref="HEAD")
    assert outcome.ok is False
    assert "not a git repository" in outcome.reason


def test_release_removes_the_worktree(repo: Path, tmp_path: Path):
    target = tmp_path / "wt" / "run__n1"
    assert provision_worktree(path=target, repo_root=str(repo), base_ref="HEAD").ok
    ok, reason = release_worktree(path=target, repo_root=str(repo))
    assert ok, reason
    assert not target.exists()


# ---------------------------------------------------------------------- engine


def _worktree_workflow(repo_root: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        id="wf_wt",
        name="worktree fixture",
        graph=WorkflowGraph(
            nodes={
                "task": WorkflowNode(id="task", type=NodeType.TOOL, executor="alpha.local.model", config={"worktree": {"repo_root": repo_root, "base_ref": "HEAD"}}),
                "wrap": WorkflowNode(id="wrap", type=NodeType.TOOL, executor="alpha.local.model"),
            },
            edges=[WorkflowEdge(source="task", target="wrap")],
        ),
        budget=100000,
    )


def _runner(node, run):  # noqa: ANN001
    return {"status": "completed", "output": f"{node.id} done", "evidence": "ok", "tokens_used": 0}


def test_node_runs_in_a_provisioned_worktree(repo: Path, tmp_path: Path):
    engine = DynamicWorkflowEngine()
    engine.register_definition(_worktree_workflow(str(repo)))
    engine.worktree_root = tmp_path / "wt"
    engine.worktree_repo_roots = {str(repo)}
    engine.worktrees = WorktreeStore(tmp_path / "store")
    run = engine.start_run("wf_wt")
    run = engine.execute_step(run.run_id, node_runner=_runner)
    info = run.state.get("task_worktree")
    assert info is not None
    assert Path(info["path"]).is_dir()
    assert info["head_commit"]
    assert any("provisioned" in line for line in engine._run_graphs[run.run_id].nodes["task"].evidence)
    claimed = [event for event in engine.events.get_events(run.run_id) if event.event_type == "worktree_claimed"]
    assert claimed and claimed[0].payload["claim_id"] == info["claim_id"]
    # The checkout is a real detached worktree of the allowed repo.
    assert (Path(info["path"]) / "file.txt").exists()
    # Release removes it and closes the claim.
    result = engine.release_node_worktree(run.run_id, "task")
    assert result["released"] is True
    assert not Path(info["path"]).exists()
    assert engine.worktrees.get(info["claim_id"]).status is WorktreeStatus.REMOVED


def test_unbound_allowlist_fails_the_node_honestly(repo: Path, tmp_path: Path):
    engine = DynamicWorkflowEngine()
    engine.register_definition(_worktree_workflow(str(repo)))
    engine.worktree_root = tmp_path / "wt"
    engine.worktrees = WorktreeStore(tmp_path / "store")
    # No worktree_repo_roots bound.
    run = engine.start_run("wf_wt")
    run = engine.execute_step(run.run_id, node_runner=_runner)
    assert run.status.value == "failed"
    failed = [event for event in engine.events.get_events(run.run_id) if event.event_type == "node_failed"]
    assert any("worktree_repo_roots allowlist" in str(event.payload) for event in failed)
    assert engine.worktrees.list() == []


def test_a_disallowed_repo_is_refused_by_name(repo: Path, tmp_path: Path):
    engine = DynamicWorkflowEngine()
    engine.register_definition(_worktree_workflow(str(repo)))
    engine.worktree_root = tmp_path / "wt"
    engine.worktree_repo_roots = {str(tmp_path / "some-other-repo")}
    engine.worktrees = WorktreeStore(tmp_path / "store")
    run = engine.start_run("wf_wt")
    run = engine.execute_step(run.run_id, node_runner=_runner)
    assert run.status.value == "failed"
    failed = [event for event in engine.events.get_events(run.run_id) if event.event_type == "node_failed"]
    assert any("not in this engine's bound worktree_repo_roots" in str(event.payload) for event in failed)


def test_provisioning_failure_fails_the_node_and_the_claim(repo: Path, tmp_path: Path):
    engine = DynamicWorkflowEngine()
    definition = _worktree_workflow(str(repo))
    definition.graph.nodes["task"].config["worktree"]["base_ref"] = "refs/heads/does-not-exist"
    engine.register_definition(definition)
    engine.worktree_root = tmp_path / "wt"
    engine.worktree_repo_roots = {str(repo)}
    engine.worktrees = WorktreeStore(tmp_path / "store")
    run = engine.start_run("wf_wt")
    run = engine.execute_step(run.run_id, node_runner=_runner)
    assert run.status.value == "failed"
    claims = engine.worktrees.list()
    assert len(claims) == 1
    assert claims[0].status is WorktreeStatus.FAILED
    assert "worktree creation failed" in claims[0].reason
