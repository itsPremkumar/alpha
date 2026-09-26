"""Phase-2 guarded source-update engine tests.

These tests use disposable Git repositories and never contact GitHub.  They
pin the safety properties that matter for unattended operation: the default is
check-only, dirty/diverged worktrees are refused, a clean fast-forward creates
a backup, and a failed health check restores the previous commit.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from alpha.evolution import release_check
from alpha.evolution.git_source import GitRemote, GitRepository
from alpha.evolution.update_engine import UpdateCandidate, UpdateEngine
from alpha.evolution.update_policy import UpdatePolicy


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture()
def source_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Create a local bare remote plus a source checkout on ``main``."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare")

    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "alpha-updater@example.invalid")
    _git(repo, "config", "user.name", "Alpha Updater Test")
    (repo / "config").mkdir()
    (repo / "backend" / "packages" / "harness").mkdir(parents=True)
    (repo / "backend" / "packages" / "harness" / "pyproject.toml").write_text(
        '[project]\nname = "agent-workspace-harness"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (repo / "config" / "project-manifest.json").write_text(
        json.dumps(
            {
                "projectId": "alpha-test",
                "name": "Alpha Test",
                "repository": {
                    "provider": "github",
                    "owner": "itsPremkumar",
                    "name": "alpha",
                    "url": "https://github.com/itsPremkumar/alpha",
                    "defaultBranch": "main",
                },
                "release": {"channel": "main"},
            }
        ),
        encoding="utf-8",
    )
    (repo / "README.md").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")

    home = tmp_path / "runtime-home"
    monkeypatch.setenv("AGENT_WORKSPACE_PROJECT_ROOT", str(repo))
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))
    monkeypatch.setenv("ALPHA_PROJECT_MANIFEST", str(repo / "config" / "project-manifest.json"))
    return repo, remote


def _policy(**overrides: object) -> UpdatePolicy:
    values: dict[str, object] = {
        "enabled": True,
        "auto_apply": False,
        "channel": "main",
        "remote": "origin",
        "branch": "main",
        "check_interval_seconds": 60,
        "jitter_seconds": 0,
        "require_clean_worktree": True,
        "allowed_branches": ("main",),
        "min_free_disk_mb": 0,
        "sync_dependencies": False,
        "run_config_upgrade": False,
        "restart_mode": "none",
        "health_check_attempts": 1,
        "health_check_interval_seconds": 0,
        "health_urls": (),
    }
    values.update(overrides)
    return UpdatePolicy(**values)  # type: ignore[arg-type]


def _engine_for(repo_path: Path, remote_path: Path, **kwargs: object) -> UpdateEngine:
    """Build an engine with a local test remote while retaining GitHub policy checks."""
    engine = UpdateEngine(
        policy=_policy(**kwargs.pop("policy_overrides", {})),  # type: ignore[arg-type]
        root=repo_path,
        repo=GitRepository(repo_path),
        **kwargs,  # type: ignore[arg-type]
    )
    # The production engine intentionally requires the manifest's GitHub URL.
    # Disposable tests use a file remote, so inject the same owner/repository
    # identity at this seam rather than weakening production validation.
    identity = GitRemote("origin", str(remote_path), "itsPremkumar", "alpha")
    engine._remote = lambda: identity  # type: ignore[method-assign]
    engine.repo.validate_checkout = lambda **_kwargs: engine.repo.status()  # type: ignore[method-assign]
    return engine


def _push_update(repo: Path) -> str:
    """Publish a new remote commit without moving the caller's checkout."""
    remote_url = _git(repo, "remote", "get-url", "origin")
    publisher = repo.parent / f"publisher-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["git", "clone", str(remote_url), str(publisher)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    _git(publisher, "config", "user.email", "alpha-publisher@example.invalid")
    _git(publisher, "config", "user.name", "Alpha Publisher Test")
    (publisher / "README.md").write_text("v2\n", encoding="utf-8")
    _git(publisher, "add", "README.md")
    _git(publisher, "commit", "-m", "second")
    _git(publisher, "push", "origin", "main")
    target = _git(publisher, "rev-parse", "HEAD")
    shutil.rmtree(publisher, ignore_errors=True)
    return target


def test_default_policy_is_safe_and_check_only() -> None:
    policy = UpdatePolicy()
    assert policy.enabled is False
    assert policy.auto_apply is False
    assert policy.require_clean_worktree is True
    assert policy.rollback_on_failure is True


def test_main_channel_discovers_fast_forward_candidate(source_repo: tuple[Path, Path]) -> None:
    repo_path, _remote = source_repo
    old = _git(repo_path, "rev-parse", "HEAD")
    target = _push_update(repo_path)
    engine = _engine_for(repo_path, _remote)

    state = engine.check(force=True)

    assert state["state"] == release_check.UPDATE_AVAILABLE
    assert state["availableVersion"].endswith(target[:12])
    assert state["targetCommit"] == target
    assert state["canApply"] is True
    assert state["currentCommit"] == old


def test_dirty_worktree_is_disclosed_and_never_fast_forwarded(source_repo: tuple[Path, Path]) -> None:
    repo_path, _remote = source_repo
    _push_update(repo_path)
    (repo_path / "operator-change.txt").write_text("keep me\n", encoding="utf-8")
    engine = _engine_for(repo_path, _remote)

    state = engine.check(force=True)

    assert state["state"] == release_check.UPDATE_AVAILABLE
    assert state["canApply"] is False
    assert "tracked or untracked changes" in state["reason"]
    assert (repo_path / "operator-change.txt").is_file()


def test_apply_creates_backup_and_health_success(source_repo: tuple[Path, Path]) -> None:
    repo_path, _remote = source_repo
    old = _git(repo_path, "rev-parse", "HEAD")
    target = _push_update(repo_path)
    engine = _engine_for(repo_path, _remote)
    checked = engine.check(force=True)
    assert checked["state"] == release_check.UPDATE_AVAILABLE

    result = engine.apply_now(candidate=UpdateCandidate.from_public_dict(checked["candidate"]), force=True)

    assert result.ok is True
    assert result.state == release_check.HEALTHY
    assert result.from_commit == old
    assert result.to_commit == target
    assert _git(repo_path, "rev-parse", "HEAD") == target
    assert any(ref.startswith("refs/alpha-update/backups/") for ref in GitRepository(repo_path).backup_refs())


def test_failed_health_check_restores_previous_commit(source_repo: tuple[Path, Path]) -> None:
    repo_path, _remote = source_repo
    old = _git(repo_path, "rev-parse", "HEAD")
    target = _push_update(repo_path)
    health_results = iter((False, True))
    engine = _engine_for(repo_path, _remote, health_check=lambda: next(health_results, True))
    checked = engine.check(force=True)

    result = engine.apply_now(candidate=UpdateCandidate.from_public_dict(checked["candidate"]), force=True)

    assert result.ok is False
    assert result.rolled_back is True
    assert result.state == release_check.FAILED_UPDATE_RECORDED
    assert _git(repo_path, "rev-parse", "HEAD") == old
    assert (repo_path / "README.md").read_text(encoding="utf-8") == "v1\n"
    assert target != old


def test_diverged_remote_target_is_blocked(source_repo: tuple[Path, Path]) -> None:
    repo_path, _remote = source_repo
    # Make a local commit that is not in the remote, then publish a different
    # remote commit.  A reset/merge would risk operator work, so check must
    # disclose the divergence instead.
    (repo_path / "local.txt").write_text("local\n", encoding="utf-8")
    _git(repo_path, "add", "local.txt")
    _git(repo_path, "commit", "-m", "local")
    _push_update(repo_path)
    engine = _engine_for(repo_path, _remote)

    state = engine.check(force=True)

    assert state["state"] == release_check.BLOCKED
    assert "fast-forward" in state["reason"]
    assert _git(repo_path, "rev-parse", "HEAD") != _git(repo_path, "rev-parse", "origin/main")


def test_apply_requires_an_explicit_candidate(source_repo: tuple[Path, Path]) -> None:
    repo_path, _remote = source_repo
    engine = _engine_for(repo_path, _remote)
    result = engine.apply_now(force=True)
    assert result.ok is False
    assert "candidate" in result.reason


def test_manual_apply_requires_explicit_confirmation_when_auto_apply_is_off(source_repo: tuple[Path, Path]) -> None:
    repo_path, remote = source_repo
    _push_update(repo_path)
    engine = _engine_for(repo_path, remote)
    checked = engine.check(force=True)
    candidate = UpdateCandidate.from_public_dict(checked["candidate"])

    result = engine.apply_now(candidate=candidate)

    assert result.ok is False
    assert "explicit operator confirmation" in result.reason
    assert _git(repo_path, "rev-parse", "HEAD") != _git(repo_path, "rev-parse", "origin/main")


def test_update_check_route_uses_transaction_engine_when_policy_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from alpha.evolution import update_policy
    from app.gateway.routers import evolution

    class FakeEngine:
        def __init__(self) -> None:
            self.calls = 0

        def check(self, *, force: bool = False) -> dict[str, object]:
            self.calls += 1
            assert force is True
            return {"state": release_check.UPDATE_AVAILABLE, "candidate": {"commit": "a" * 40}}

    fake = FakeEngine()
    monkeypatch.setattr(update_policy, "load_update_policy", lambda: UpdatePolicy(enabled=True, auto_apply=False))
    monkeypatch.setattr("alpha.evolution.update_engine.get_update_engine", lambda: fake)
    app = FastAPI()
    app.include_router(evolution.router)
    with TestClient(app) as client:
        response = client.post("/api/evolution/update-check")

    assert response.status_code == 200
    assert response.json()["state"] == release_check.UPDATE_AVAILABLE
    assert fake.calls == 1


def test_update_apply_route_is_admin_only_and_uses_server_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway.routers import evolution

    class FakeEngine:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        def request_apply(self, *, force: bool = False) -> dict[str, object]:
            self.calls.append(force)
            return {"ok": True, "state": "APPLY_REQUESTED", "transaction_id": "upd-test"}

    fake = FakeEngine()
    monkeypatch.setattr("alpha.evolution.update_engine.get_update_engine", lambda: fake)
    app = FastAPI()

    @app.middleware("http")
    async def stamp_user(request, call_next):
        request.state.user = SimpleNamespace(system_role="admin")
        return await call_next(request)

    app.include_router(evolution.router)
    with TestClient(app) as client:
        response = client.post("/api/evolution/update-apply", json={"force": True})

    assert response.status_code == 202
    assert response.json()["transaction_id"] == "upd-test"
    assert fake.calls == [True]


def test_self_update_tick_is_zero_activity_when_policy_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from alpha.evolution import update_policy
    from app.gateway.autonomy import loops

    monkeypatch.setattr(update_policy, "load_update_policy", lambda: UpdatePolicy())

    result = loops.self_update_tick()

    assert result == {"state": "DISABLED", "checked": False, "reason": "auto-update policy is disabled"}


def test_update_apply_route_rejects_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway.routers import evolution

    app = FastAPI()

    @app.middleware("http")
    async def stamp_user(request, call_next):
        request.state.user = SimpleNamespace(system_role="user")
        return await call_next(request)

    app.include_router(evolution.router)
    with TestClient(app) as client:
        response = client.post("/api/evolution/update-apply", json={})

    assert response.status_code == 403


def test_force_never_bypasses_a_persisted_can_apply_false(source_repo: tuple[Path, Path]) -> None:
    repo_path, remote = source_repo
    _push_update(repo_path)
    (repo_path / "operator-change.txt").write_text("keep me\n", encoding="utf-8")
    engine = _engine_for(repo_path, remote)
    checked = engine.check(force=True)
    assert checked["canApply"] is False

    result = engine.apply_now(candidate=UpdateCandidate.from_public_dict(checked["candidate"]), force=True)

    assert result.ok is False
    assert result.state == release_check.UPDATE_AVAILABLE
    assert _git(repo_path, "rev-parse", "HEAD") != _git(repo_path, "rev-parse", "origin/main")
    assert (repo_path / "operator-change.txt").is_file()


def test_recovery_before_mutation_records_failure_without_reset(source_repo: tuple[Path, Path]) -> None:
    repo_path, remote = source_repo
    engine = _engine_for(repo_path, remote)
    transaction_id = "upd-abcdef0123456789"
    engine.state_writer.transition(
        release_check.APPLY_REQUESTED,
        transactionId=transaction_id,
        candidate=None,
        mutationStarted=False,
        reason="detached update transaction requested",
    )

    result = engine.recover_incomplete()

    assert result.ok is True
    assert result.state == release_check.FAILED_UPDATE_RECORDED
    state = release_check.load_update_state()
    assert state["transactionId"] == transaction_id
    assert state["mutationStarted"] is False


def test_unsafe_http_github_remote_is_rejected() -> None:
    from alpha.evolution.git_source import GitRepositoryError, canonical_remote

    with pytest.raises(GitRepositoryError):
        canonical_remote("origin", "http://github.com/itsPremkumar/alpha.git")
    with pytest.raises(GitRepositoryError):
        canonical_remote("origin", "https://user:secret@github.com/itsPremkumar/alpha.git")


def test_update_state_and_history_redact_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "home"))
    from alpha.evolution.update_state import StateWriter, history

    writer = StateWriter()
    state = writer.transition(
        release_check.CHECKING,
        reason="Authorization: Bearer super-secret GITHUB_TOKEN=also-secret https://user:pass@github.com/x/y",
    )
    assert "super-secret" not in str(state)
    assert "also-secret" not in str(state)
    assert "user:pass" not in str(state)
    assert all("super-secret" not in str(item) for item in history())


def test_update_apply_route_rejects_auth_disabled_synthetic_admin() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway.routers import evolution

    app = FastAPI()

    @app.middleware("http")
    async def stamp_auth_disabled(request, call_next):
        from app.gateway.auth_disabled import AUTH_SOURCE_AUTH_DISABLED

        request.state.user = SimpleNamespace(system_role="admin")
        request.state.auth_source = AUTH_SOURCE_AUTH_DISABLED
        return await call_next(request)

    app.include_router(evolution.router)
    with TestClient(app) as client:
        response = client.post("/api/evolution/update-apply", json={})

    assert response.status_code == 403
