"""Tests for WP-B1: isolated RSI candidate workspaces (rsi/workspace.py).

Pins (plan section 3 WP-B1):
- worktrees are created on branch ``rsi/<candidate_id>`` at repo HEAD with
  ``kind="worktree"`` / ``assurance="standard"``; ``destroy`` removes both the
  directory and the branch;
- ``main``/``master``/any non-``rsi/*`` branch name raises ``ValueError``
  before any git call ("never touch main", mechanical);
- guarded commands: SelfRepoGuard and SafetyGuard violations return
  ``(False, reason)`` fail-closed and the checker's output is never swallowed;
- git-unavailable → ``kind="copy"``, ``assurance="lower"`` — honestly present
  in the persisted record AND in every evidence line ``run_checks`` emits;
- orphan reconciliation: a recorded-but-deleted path is REPORTED by
  ``load()``, never silently dropped;
- the context manager leaves no workspace behind, also on exceptions;
- the protected-path seam is the single integration point and is stubbed in
  tests (single-seam honesty pattern): present → classify disclosure +
  deny-block; absent → real ImportError text disclosed, two guards alone.
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import alpha.rsi.workspace as workspace_mod
from alpha.config.runtime_paths import project_root, runtime_home
from alpha.rsi.workspace import (
    CandidateWorkspace,
    RsiWorkspaceManager,
    _protected_paths_api,
    _validate_branch_name,
)
from alpha.safety.self_repo_guard import SelfRepoGuard
from alpha.sandbox.worktrees import WorktreeManager


@pytest.fixture(autouse=True)
def _isolate_runtime_home(tmp_path, monkeypatch):
    """Every test gets its own state dir: AGENT_WORKSPACE_HOME -> tmp_path (task-mandated)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_WORKSPACE_PROJECT_ROOT", raising=False)
    yield


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, timeout=20)


@pytest.fixture
def repo(tmp_path):
    """A throwaway git repository (never Alpha's own repo — no Alpha history is touched)."""
    root = tmp_path / "wt-src-repo"
    root.mkdir()
    _git(root, "init")
    (root / "sample.txt").write_text("baseline content", encoding="utf-8")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "add", "sample.txt")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline")
    return root


@pytest.fixture
def mgr(repo):
    # base_worktree_dir defaults to runtime_home()/rsi/worktrees (asserted separately).
    return RsiWorkspaceManager(repo_root=repo)


def _branch_names(manager):
    return [item.get("branch") for item in manager.list_worktrees()]


# ── defaults ───────────────────────────────────────────────────────────────


def test_manager_defaults_match_plan():
    default_mgr = RsiWorkspaceManager()
    assert default_mgr.repo_root == project_root() == Path.cwd().resolve()
    assert default_mgr.base_worktree_dir == (runtime_home() / "rsi" / "worktrees").resolve()
    assert default_mgr.records_path == runtime_home() / "rsi" / "workspaces.json"
    assert default_mgr.copy_ws_root == runtime_home() / "rsi" / "copy_ws"


# ── worktree lifecycle ─────────────────────────────────────────────────────


def test_create_worktree_at_repo_head(mgr, repo):
    ws = mgr.create("cand-01")
    assert ws.kind == "worktree"
    assert ws.assurance == "standard"
    assert ws.branch == "rsi/cand-01"
    assert ws.path.is_dir()
    assert (ws.path / ".git").is_file()
    assert (ws.path / "sample.txt").read_text(encoding="utf-8") == "baseline content"
    # under the managed base dir (WorktreeManager names dirs wt-<hash>, drift noted in report)
    assert ws.path.resolve().is_relative_to(mgr.base_worktree_dir)
    assert git_head(ws.path) == "refs/heads/rsi/cand-01"
    # at repo HEAD
    entry = next(item for item in mgr.list_worktrees() if Path(item["path"]).resolve() == ws.path.resolve())
    assert entry["head"] == _git(repo, "rev-parse", "HEAD").stdout.strip()
    # persisted record
    payload = json.loads(mgr.records_path.read_text(encoding="utf-8"))
    stored = payload["workspaces"][ws.workspace_id]
    assert stored["candidate_id"] == "cand-01"
    assert stored["branch"] == "rsi/cand-01"
    assert stored["assurance"] == "standard"
    assert stored["kind"] == "worktree"
    assert "score" not in stored and "confidence" not in stored  # no fabricated metrics anywhere


def git_head(path):
    out = subprocess.run(
        ["git", "-C", str(path), "symbolic-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return out.stdout.strip()


def test_destroy_removes_branch_and_record(mgr):
    ws = mgr.create("cand-02")
    removed_path = ws.path
    assert mgr.destroy(ws) is True
    assert not removed_path.exists()
    assert "refs/heads/rsi/cand-02" not in _branch_names(mgr)
    payload = json.loads(mgr.records_path.read_text(encoding="utf-8"))
    assert ws.workspace_id not in payload["workspaces"]
    # destroying again reports honestly instead of pretending
    assert mgr.destroy(ws) is False


# ── never touch main ───────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["main", "master", "feature/x", "agent/task", "", "rsi/", "rsi/../evil", "rsi/a b", "rsi/a..b", "rsi/x.lock", "rsi/.hidden", None])
def test_validate_branch_name_never_touch_main(bad):
    with pytest.raises(ValueError):
        _validate_branch_name(bad)


def test_validate_branch_name_accepts_rsi_prefix():
    assert _validate_branch_name("rsi/cand-ok") == "rsi/cand-ok"


def test_create_composes_only_rsi_branches(mgr, repo):
    """create('main') composes rsi/main; the repo's own primary branch is never switched or mutated."""
    before = set(_branch_names(mgr))
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    sym_before = _git(repo, "symbolic-ref", "HEAD").stdout.strip()
    ws = mgr.create("main")  # branch is rsi/main — refs/heads/main is never created or checked out
    try:
        assert ws.branch == "rsi/main"
        assert "refs/heads/rsi/main" in set(_branch_names(mgr))
        assert set(_branch_names(mgr)) - before == {"refs/heads/rsi/main"}  # only our branch was added
    finally:
        assert mgr.destroy(ws) is True
    assert set(_branch_names(mgr)) == before  # destroy removed exactly what create added
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before
    assert _git(repo, "symbolic-ref", "HEAD").stdout.strip() == sym_before


@pytest.mark.parametrize("bad_id", ["", "../main", "a/b", "a b", ".hidden", "a..b", "x.lock", None, "main/../../evil"])
def test_create_rejects_malformed_candidate_ids(mgr, bad_id):
    with pytest.raises(ValueError):
        mgr.create(bad_id)


def test_destroy_refuses_non_rsi_branch(mgr, tmp_path):
    ws = CandidateWorkspace(
        workspace_id="forged",
        kind="worktree",
        path=mgr.base_worktree_dir / "wt-forged",
        branch="main",
        assurance="standard",
        created_at=time.time(),
    )
    with pytest.raises(ValueError):
        mgr.destroy(ws)  # mechanical never-touch-main: rejected before any git call


# ── honest failure: the real git error surfaces ────────────────────────────


def test_create_surfaces_real_git_error(mgr, repo, monkeypatch):
    def explode(self, args):
        raise subprocess.CalledProcessError(128, ["git", *args], output="", stderr="fatal: not a git repository (or any parent): .git")

    monkeypatch.setattr(WorktreeManager, "_run_git", explode)
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        mgr.create("cand-err")
    # The real git error surfaces (CalledProcessError carries git's stderr verbatim)
    assert "fatal: not a git repository" in (excinfo.value.stderr or "")
    # workspace creation never silently returns a fake path and records nothing
    payload = json.loads(mgr.records_path.read_text(encoding="utf-8")) if mgr.records_path.exists() else {"workspaces": {}}
    assert payload["workspaces"] == {}


def test_create_surfaces_invalid_base_ref(mgr, repo):
    with pytest.raises(ValueError):
        mgr.create("cand-badbase", base_ref="--end-of-options")
    with pytest.raises(subprocess.CalledProcessError):
        mgr.create("cand-badref", base_ref="no-such-ref-hopefully")
    payload = json.loads(mgr.records_path.read_text(encoding="utf-8")) if mgr.records_path.exists() else {"workspaces": {}}
    assert payload["workspaces"] == {}


# ── git-unavailable → copy workspace, assurance honestly recorded ──────────


def test_git_unavailable_falls_back_to_copy(mgr, repo, monkeypatch):
    def no_git(self, args):
        raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(WorktreeManager, "_run_git", no_git)
    ws = mgr.create("cand-copy-1")
    assert ws.kind == "copy"
    assert ws.assurance == "lower"
    assert ws.branch is None  # a copy workspace has no git branch — never fabricated
    assert ws.path == runtime_home() / "rsi" / "copy_ws" / "cand-copy-1"
    assert (ws.path / "sample.txt").read_text(encoding="utf-8") == "baseline content"
    assert not (ws.path / ".git").exists()  # bounded copy: VCS metadata excluded
    payload = json.loads(mgr.records_path.read_text(encoding="utf-8"))
    stored = payload["workspaces"][ws.workspace_id]
    assert stored["assurance"] == "lower"
    assert stored["kind"] == "copy"
    # the REAL reason git was unavailable is recorded, not paraphrased away
    notes = " ".join(stored.get("notes", []))
    assert "git worktree unavailable" in notes
    assert "No such file or directory" in notes


def test_create_copy_lower_assurance_recorded(mgr):
    ws = mgr.create_copy("cand-copy-direct")
    assert ws.kind == "copy"
    assert ws.assurance == "lower"
    payload = json.loads(mgr.records_path.read_text(encoding="utf-8"))
    stored = payload["workspaces"][ws.workspace_id]
    assert stored["assurance"] == "lower"
    assert stored.get("notes")  # why it is lower-assurance is disclosed in the record
    # honest failure: a second copy to the same path surfaces the real error
    with pytest.raises(FileExistsError):
        mgr.create_copy("cand-copy-direct")


# ── run_checks: guards first, output never swallowed ───────────────────────


def test_run_checks_success_carries_checker_output_and_no_fabrication(mgr):
    ws = mgr.create("cand-check-ok")
    ok, detail = mgr.run_checks(ws, [sys.executable, "-c", "print('check-output-marker')"], timeout=60)
    assert ok is True
    assert "check-output-marker" in detail
    assert "exit code 0" in detail
    assert "assurance=standard" in detail
    api, _ = _protected_paths_api()
    if api is None:
        assert "protected-path classification unavailable" in detail
    else:
        assert "protected-path: " in detail
    # no fabricated metrics in any evidence line
    assert "score" not in detail
    assert "confidence" not in detail


def test_run_checks_self_repo_guard_blocks_before_running(mgr, monkeypatch):
    ws = mgr.create("cand-check-guard")
    # Model the production layout (runtime home inside the running checkout):
    # SelfRepoGuard flags mutating git commands whose cwd sits inside its
    # running_repo_root.  Its own behaviour is pinned in
    # test_self_repo_integrity_guard.py; here we pin run_checks' integration.
    tree_root = Path(ws.path).parents[2]  # <tmp_path> containing rsi/worktrees/wt-*
    monkeypatch.setattr(workspace_mod, "get_self_repo_guard", lambda: SelfRepoGuard(running_repo_root=tree_root))
    ok, reason = mgr.run_checks(ws, ["git", "reset", "--hard"], timeout=60)
    assert ok is False
    assert "Self-repo mutation blocked" in reason  # verbatim guard reason, never swallowed
    assert "reset --hard" in reason
    assert "exit code" not in reason  # nothing was executed


def test_run_checks_safety_guard_blocks_destructive_command(mgr):
    ws = mgr.create("cand-check-safety")
    ok, reason = mgr.run_checks(ws, ["rm", "-rf", "/"], timeout=60)
    assert ok is False
    assert "Command blocked by safety guardrail" in reason
    assert "exit code" not in reason


def test_run_checks_reports_failure_output_not_swallowed(mgr):
    ws = mgr.create("cand-check-fail")
    ok, detail = mgr.run_checks(ws, [sys.executable, "-c", "import sys; print('boom-stderr', file=sys.stderr); sys.exit(3)"], timeout=60)
    assert ok is False
    assert "exit code 3" in detail
    assert "boom-stderr" in detail  # checker output surfaced, not swallowed


def test_run_checks_timeout_is_reported(mgr):
    ws = mgr.create("cand-check-timeout")
    ok, detail = mgr.run_checks(ws, [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert ok is False
    assert "timed out after" in detail


def test_run_checks_invalid_inputs_fail_closed(mgr):
    ws = mgr.create("cand-check-invalid")
    assert mgr.run_checks(ws, [], timeout=5)[0] is False
    assert mgr.run_checks(ws, "rm -rf /", timeout=5)[0] is False
    assert mgr.run_checks(ws, ["echo", 1], timeout=5)[0] is False
    assert mgr.run_checks(ws, ["echo", "hi"], timeout=0)[0] is False
    missing = CandidateWorkspace(
        workspace_id="gone",
        kind="worktree",
        path=mgr.base_worktree_dir / "wt-does-not-exist",
        branch="rsi/gone",
        assurance="standard",
        created_at=time.time(),
    )
    ok, detail = mgr.run_checks(missing, ["echo", "hi"], timeout=5)
    assert ok is False
    assert "workspace path does not exist" in detail


def test_run_checks_lower_assurance_evidence_line(mgr, monkeypatch):
    def no_git(self, args):
        raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(WorktreeManager, "_run_git", no_git)
    ws = mgr.create("cand-copy-evidence")
    assert ws.assurance == "lower"
    ok, detail = mgr.run_checks(ws, [sys.executable, "-c", "print('copy-check-ok')"], timeout=60)
    assert ok is True
    assert "copy-check-ok" in detail
    assert "assurance=lower" in detail  # lower assurance appears in every emitted evidence line
    assert "copy workspace" in detail


# ── protected-path seam (single-seam honesty pattern; tests stub it) ───────


def test_protected_paths_seam_presence_is_honest():
    api, note = _protected_paths_api()
    if api is None:
        # absent: the real ImportError text, verbatim — never a fabricated classification
        assert "protected_paths" in note
        assert note.strip()
    else:
        assert callable(api["classify"])
        assert callable(api["assert_candidate_path_allowed"])
        assert note == ""


def test_run_checks_seam_absent_discloses_real_import_error(mgr, monkeypatch):
    ws = mgr.create("cand-seam-absent")
    monkeypatch.setattr(
        workspace_mod,
        "_protected_paths_api",
        lambda: (None, "ModuleNotFoundError: No module named 'alpha.rsi.protected_paths'"),
    )
    ok, detail = mgr.run_checks(ws, [sys.executable, "-c", "print('two-guards-ok')"], timeout=60)
    assert ok is True  # proceeds on the two safety guards alone
    assert "protected-path classification unavailable: ModuleNotFoundError: No module named 'alpha.rsi.protected_paths'" in detail
    assert "two-guards-ok" in detail


def test_run_checks_seam_deny_blocks_before_execution(mgr, monkeypatch):
    ws = mgr.create("cand-seam-deny")
    api = {
        "classify": lambda path: ("deny", "**/.env*") if str(path).endswith(".env") else ("review_required", ""),
        "assert_candidate_path_allowed": lambda path, reviewed=False: None,
    }
    monkeypatch.setattr(workspace_mod, "_protected_paths_api", lambda: (api, ""))
    ok, reason = mgr.run_checks(ws, [sys.executable, "-c", "print('never runs')", ".env"], timeout=60)
    assert ok is False
    assert "protected-path policy denied" in reason
    assert ".env" in reason
    assert "matched pattern: **/.env*" in reason
    assert "exit code" not in reason  # denied before any subprocess


def test_run_checks_seam_review_required_is_disclosed_not_approved(mgr, monkeypatch):
    ws = mgr.create("cand-seam-review")
    api = {
        "classify": lambda path: ("review_required", ""),
        "assert_candidate_path_allowed": lambda path, reviewed=False: None,
    }
    monkeypatch.setattr(workspace_mod, "_protected_paths_api", lambda: (api, ""))
    ok, detail = mgr.run_checks(ws, [sys.executable, "-c", "print('runs-with-disclosure')"], timeout=60)
    assert ok is True
    assert "review_required" in detail
    assert "disclosure, not approval" in detail  # never claims review it cannot grant


def test_run_checks_seam_classifier_error_fails_closed(mgr, monkeypatch):
    ws = mgr.create("cand-seam-broken")

    def broken_classify(path):
        raise RuntimeError("classifier bug")

    api = {"classify": broken_classify, "assert_candidate_path_allowed": lambda path, reviewed=False: None}
    monkeypatch.setattr(workspace_mod, "_protected_paths_api", lambda: (api, ""))
    ok, detail = mgr.run_checks(ws, [sys.executable, "-c", "print('never runs')"], timeout=60)
    assert ok is False
    assert "protected-path classification failed" in detail
    assert "classifier bug" in detail  # the real error, verbatim


# ── persistence: orphan reconciliation on load ─────────────────────────────


def test_orphan_reconciliation_reports_deleted_path(mgr, repo):
    ws = mgr.create("cand-orphan")
    fresh = RsiWorkspaceManager(repo_root=repo)
    report = fresh.load()
    assert [item.workspace_id for item in report["active"]] == [ws.workspace_id]
    assert report["orphans"] == []
    shutil.rmtree(ws.path)  # workspace vanished (crash/cleanup outside this process)
    again = RsiWorkspaceManager(repo_root=repo)
    report = again.load()
    assert report["active"] == []
    assert len(report["orphans"]) == 1
    orphan = report["orphans"][0]
    assert orphan["state"] == "orphan_missing_path"
    assert orphan["workspace_id"] == ws.workspace_id
    assert orphan["candidate_id"] == "cand-orphan"
    assert "no longer exists" in orphan["reason"]
    # REPORTED, not silently dropped: the record is still on disk
    payload = json.loads(again.records_path.read_text(encoding="utf-8"))
    assert ws.workspace_id in payload["workspaces"]


def test_invalid_record_reported_not_dropped(mgr):
    mgr.records_path.parent.mkdir(parents=True, exist_ok=True)
    mgr.records_path.write_text(
        json.dumps(
            {
                "version": 1,
                "workspaces": {
                    "missing-field": {
                        "workspace_id": "missing-field",
                        "kind": "copy",
                        "path": str(mgr.copy_ws_root / "missing-field"),
                        "branch": None,
                        "assurance": "lower",
                        "created_at": 1.0,
                        # candidate_id intentionally missing
                    },
                    "not-a-dict": "garbage",
                },
            }
        ),
        encoding="utf-8",
    )
    fresh = RsiWorkspaceManager(repo_root=mgr.repo_root)
    report = fresh.load()
    assert report["active"] == []
    states = {item["workspace_id"]: item["state"] for item in report["orphans"]}
    assert states == {"missing-field": "invalid_record", "not-a-dict": "invalid_record"}
    payload = json.loads(fresh.records_path.read_text(encoding="utf-8"))
    assert set(payload["workspaces"]) == {"missing-field", "not-a-dict"}  # nothing dropped


def test_corrupt_records_surface_real_error(mgr):
    mgr.records_path.parent.mkdir(parents=True, exist_ok=True)
    mgr.records_path.write_text("{ this is not json", encoding="utf-8")
    fresh = RsiWorkspaceManager(repo_root=mgr.repo_root)
    with pytest.raises(RuntimeError) as excinfo:
        fresh.load()
    message = str(excinfo.value)
    assert "could not read workspace records" in message
    assert "workspaces.json" in message
    assert "Expecting" in message  # the real parser error is carried through, not paraphrased


# ── context manager: mirrors worktree_context(delete_on_exit=True) ─────────


def test_workspace_context_cleans_up(mgr):
    with mgr.workspace("cand-ctx") as ws:
        inside_path = ws.path
        assert inside_path.is_dir()
        payload = json.loads(mgr.records_path.read_text(encoding="utf-8"))
        assert ws.workspace_id in payload["workspaces"]
    assert not inside_path.exists()
    payload = json.loads(mgr.records_path.read_text(encoding="utf-8"))
    assert payload["workspaces"] == {}


def test_workspace_context_cleans_up_on_exception(mgr):
    record_path = mgr.records_path
    with pytest.raises(RuntimeError, match="boom"):
        with mgr.workspace("cand-ctx-exc") as ws:
            inside_path = ws.path
            assert inside_path.is_dir()
            raise RuntimeError("boom")
    assert not inside_path.exists()  # no worktree left behind
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert payload["workspaces"] == {}


def test_workspace_context_copy_cleanup(mgr, monkeypatch):
    def no_git(self, args):
        raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(WorktreeManager, "_run_git", no_git)
    with mgr.workspace("cand-ctx-copy") as ws:
        copy_path = ws.path
        assert ws.kind == "copy"
        assert ws.assurance == "lower"
        assert copy_path.is_dir()
    assert not copy_path.exists()  # copy workspace destroyed on exit
    payload = json.loads(mgr.records_path.read_text(encoding="utf-8"))
    assert payload["workspaces"] == {}
