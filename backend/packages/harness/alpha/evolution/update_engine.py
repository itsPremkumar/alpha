"""Phase-2 source update engine: check, stage, install, verify, rollback.

The engine is intentionally a *source-checkout* transaction.  It uses Git's
object database as the staging/verification mechanism instead of downloading
an arbitrary archive and unpacking it over a running application.  The
sequence is durable at every boundary:

``CHECKING -> UPDATE_AVAILABLE -> STAGING -> BACKUP_CREATED -> STOPPING ->
INSTALLING -> READY_TO_SWITCH -> RESTARTING -> HEALTH_CHECK -> HEALTHY``

A failure after the backup exists follows ``ROLLBACK -> RESTORE`` and ends at
``FAILED_UPDATE_RECORDED``.  The old implementation's detection functions are
still used for the stable GitHub release HTTP seam, so existing API clients
remain compatible.

Security boundaries:

* a clean worktree and a fast-forward-only Git history are mandatory;
* the configured remote must match the repository manifest's GitHub identity;
* refs are validated and every subprocess uses an argv list (never a shell);
* the Gateway only *requests* an apply and launches a detached transaction;
  the running request handler never rewrites its own source tree;
* configuration/data are backed up outside Git and restored on rollback;
* health must pass before an update is reported successful.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from alpha.config.runtime_paths import project_root, runtime_home
from alpha.evolution import release_check
from alpha.evolution.git_source import (
    GitCommandError,
    GitRemote,
    GitRepository,
    GitRepositoryError,
    canonical_remote,
)
from alpha.evolution.manifest import load_project_manifest
from alpha.evolution.update_policy import UpdatePolicy, load_update_policy
from alpha.evolution.update_state import (
    FileUpdateLock,
    StateWriter,
    UpdateBusyError,
    UpdateStateError,
    clear_maintenance,
    history,
    load_state,
    maintenance_active,
    redact_update_text,
    runtime_has_active_work,
    safe_state_snapshot,
    write_maintenance,
)

logger = logging.getLogger(__name__)


class UpdateBlocked(RuntimeError):
    """A candidate is real but policy/worktree preconditions block applying it."""


class UpdateTransactionError(RuntimeError):
    """The update transaction could not complete safely."""


@dataclass(frozen=True, slots=True)
class UpdateCandidate:
    """A validated, immutable update target."""

    version: str
    ref: str
    commit: str
    channel: str
    source: str
    release_url: str | None = None
    prerelease: bool = False
    minimum_version: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "ref": self.ref,
            "commit": self.commit,
            "channel": self.channel,
            "source": self.source,
            "release_url": self.release_url,
            "prerelease": self.prerelease,
            "minimum_version": self.minimum_version,
        }

    @classmethod
    def from_public_dict(cls, value: dict[str, Any] | None) -> UpdateCandidate | None:
        if not isinstance(value, dict):
            return None
        required = ("version", "ref", "commit", "channel", "source")
        if any(not isinstance(value.get(key), str) or not value[key].strip() for key in required):
            return None
        if "prerelease" in value and not isinstance(value.get("prerelease"), bool):
            return None
        commit = value["commit"].strip().lower()
        if len(commit) < 7 or any(ch not in "0123456789abcdef" for ch in commit):
            return None
        return cls(
            version=value["version"].strip(),
            ref=value["ref"].strip(),
            commit=commit,
            channel=value["channel"].strip().lower(),
            source=value["source"].strip(),
            release_url=value.get("release_url") if isinstance(value.get("release_url"), str) else None,
            prerelease=value.get("prerelease", False),
            minimum_version=value.get("minimum_version") if isinstance(value.get("minimum_version"), str) else None,
        )


@dataclass(slots=True)
class UpdateResult:
    """Outcome returned by CLI/API transaction helpers."""

    ok: bool
    state: str
    reason: str = ""
    transaction_id: str | None = None
    from_commit: str | None = None
    to_commit: str | None = None
    rolled_back: bool = False

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_reason(value: object) -> str:
    return redact_update_text(value) or type(value).__name__


def _valid_transaction_id(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"(?:upd-[a-f0-9]{16}|recover-[a-f0-9]{12,32})", value))


def _parse_version(value: str) -> Version | None:
    try:
        return Version(value.strip().lstrip("vV"))
    except (InvalidVersion, AttributeError):
        return None


def _is_newer(candidate: str, installed: str) -> bool | None:
    latest = _parse_version(candidate)
    current = _parse_version(installed)
    if latest is None or current is None:
        return None
    return latest > current


def _version_was_skipped(version: str, skipped: object) -> bool:
    if not isinstance(skipped, list):
        return False
    parsed = _parse_version(version)
    if parsed is not None:
        return any(_parse_version(item) == parsed for item in skipped if isinstance(item, str))
    return version in skipped


def _repo_root() -> Path:
    return project_root().resolve()


def _canonical_remote_from_manifest(policy: UpdatePolicy) -> GitRemote:
    manifest = load_project_manifest()
    repository = manifest.get("repository")
    if not isinstance(repository, dict):
        raise UpdateTransactionError("Project manifest has no repository object")
    url = repository.get("url")
    if not isinstance(url, str) or not url.strip():
        raise UpdateTransactionError("Project manifest has no repository URL")
    return canonical_remote(policy.remote, url)


def _trusted_github_url(value: str, remote: GitRemote, *, path_prefix: str = "/") -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    expected_prefix = f"/{remote.owner}/{remote.repository}{path_prefix}"
    return bool(parsed.scheme == "https" and parsed.hostname == "github.com" and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.path.startswith(expected_prefix))


def _copy_if_present(source: Path, destination: Path) -> bool:
    if not source.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def _restore_config_snapshot(snapshot_dir: Path, root: Path) -> list[str]:
    restored: list[str] = []
    for name in ("config.yaml", "extensions_config.json"):
        source = snapshot_dir / name
        absent_marker = snapshot_dir / f".{name}.absent"
        target = root / name
        if absent_marker.is_file():
            with suppress(OSError):
                target.unlink(missing_ok=True)
            restored.append(name)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.rollback.tmp")
            try:
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
            finally:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
            restored.append(name)
    return restored


def _run_argv(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run one operator-owned command without a shell."""
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise UpdateTransactionError(f"Required update command is unavailable: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateTransactionError(f"Update command timed out: {argv[0]}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().replace("\x00", "")
        raise UpdateTransactionError(f"Update command {argv[0]} failed with exit {result.returncode}: {detail[-1_200:] or 'no output'}")
    return result


def _find_bash() -> str | None:
    if platform.system().lower() == "windows":
        candidates = [
            shutil.which("bash"),
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin" / "bash.exe"),
            shutil.which("sh"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists() and "windows\\system32" not in str(candidate).lower():
                return candidate
        return None
    for name in ("bash", "sh"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _running_in_container() -> bool:
    return bool(os.getenv("AGENT_WORKSPACE_IN_CONTAINER")) or Path("/.dockerenv").is_file()


def _deployment_mode() -> str:
    """Classify the runtime that owns this checkout."""
    configured = os.getenv("AGENT_WORKSPACE_DEPLOYMENT_MODE", "").strip().lower()
    if configured in {"docker", "container", "kubernetes", "helm", "electron", "local_source"}:
        return "local_source" if configured == "local_source" else configured
    if _running_in_container():
        return "docker" if not os.getenv("KUBERNETES_SERVICE_HOST") else "kubernetes"
    if os.getenv("ELECTRON_RUN_AS_NODE") or os.getenv("ALPHA_ELECTRON"):
        return "electron"
    return "local_source"


def _deployment_can_self_update(mode: str, restart_mode: str) -> bool:
    # ``restart_mode=none`` is an explicit operator choice for a checkout whose
    # process manager is external; it still permits a source-only transaction.
    try:
        workers = int(os.getenv("GATEWAY_WORKERS", "1"))
    except (TypeError, ValueError):
        workers = 2
    return mode == "local_source" and workers <= 1 and restart_mode in {"dev", "prod", "none"}


def _default_stop(root: Path, mode: str) -> None:
    """Stop only the local source deployment before a checkout switch."""
    if mode == "none":
        return
    if platform.system().lower() == "windows":
        stop_script = root / "stop.ps1"
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell or not stop_script.is_file():
            raise UpdateTransactionError("Windows source update requires stop.ps1 and powershell.exe")
        _run_argv(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(stop_script)],
            cwd=root,
            timeout_seconds=120,
        )
        return

    serve_script = root / "scripts" / "serve.sh"
    shell = _find_bash()
    if not shell or not serve_script.is_file():
        raise UpdateTransactionError("POSIX source update requires scripts/serve.sh and bash/sh")
    _run_argv([shell, str(serve_script), "--stop"], cwd=root, timeout_seconds=120)


def _default_start(root: Path, mode: str) -> None:
    """Start the local source deployment after a verified switch."""
    if mode == "none":
        return
    if platform.system().lower() == "windows":
        start_script = root / "start.ps1"
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell or not start_script.is_file():
            raise UpdateTransactionError("Windows source update requires start.ps1 and powershell.exe")
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        start_args = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(start_script),
            "-NoBrowser",
            "-WatchdogMode",
        ]
        if mode == "prod":
            start_args.append("-Prod")
        subprocess.Popen(
            start_args,
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return

    serve_script = root / "scripts" / "serve.sh"
    shell = _find_bash()
    if not shell or not serve_script.is_file():
        raise UpdateTransactionError("POSIX source update requires scripts/serve.sh and bash/sh")
    subprocess.Popen(
        [shell, str(serve_script), f"--{mode}", "--daemon"],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _default_restart(root: Path, mode: str) -> None:
    """Stop and relaunch the local source deployment, never Docker/Helm."""
    _default_stop(root, mode)
    _default_start(root, mode)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _default_health(urls: tuple[str, ...], *, timeout_seconds: float) -> bool:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    for url in urls:
        request = urllib.request.Request(url, headers={"User-Agent": "Alpha-Update-Health/1"})
        try:
            with opener.open(request, timeout=timeout_seconds) as response:  # noqa: S310 - policy restricts hosts to loopback
                final_url = urllib.parse.urlsplit(response.geturl())
                if final_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                    return False
                if not 200 <= int(response.status) < 400:
                    return False
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError):
            return False
    return True


class UpdateEngine:
    """Coordinate update discovery and source-checkout transactions."""

    def __init__(
        self,
        *,
        policy: UpdatePolicy | None = None,
        repo: GitRepository | None = None,
        state_writer: StateWriter | None = None,
        root: Path | None = None,
        is_idle: Callable[[], bool] | None = None,
        stop: Callable[[], None] | None = None,
        restart: Callable[[], None] | None = None,
        health_check: Callable[[], bool] | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.root = (root or _repo_root()).resolve()
        self._policy_auto_loaded = policy is None
        self.policy = policy or load_update_policy()
        self.repo = repo or GitRepository(self.root, remote=self.policy.remote)
        self.state_writer = state_writer or StateWriter()
        self._is_idle = is_idle or (lambda: not runtime_has_active_work())
        self._stop = stop
        self._restart = restart
        self._health_check = health_check
        self._command_runner = command_runner or _run_argv

    def set_idle_callback(self, callback: Callable[[], bool]) -> None:
        """Install the runtime's active-work guard for unattended applies."""
        if not callable(callback):
            raise TypeError("update idle callback must be callable")
        self._is_idle = callback

    def set_policy(self, policy: UpdatePolicy) -> None:
        """Apply a freshly loaded operator policy without losing callbacks."""
        if not isinstance(policy, UpdatePolicy):
            raise TypeError("update policy must be an UpdatePolicy")
        self.policy = policy
        if getattr(self.repo, "remote", None) != policy.remote:
            self.repo = GitRepository(self.root, remote=policy.remote)

    def _refresh_auto_policy(self) -> None:
        if not self._policy_auto_loaded:
            return
        try:
            current = load_update_policy()
        except Exception as exc:
            raise UpdateBlocked(f"could not reload update policy: {exc}") from exc
        if current != self.policy:
            self.set_policy(current)

    # -- discovery ---------------------------------------------------------

    def _remote(self) -> GitRemote:
        return _canonical_remote_from_manifest(self.policy)

    def _status(self):
        return self.repo.validate_checkout(expected_remote=self._remote())

    def _validate_candidate_shape(self, candidate: UpdateCandidate) -> None:
        """Re-check persisted candidate metadata before any mutation."""
        if candidate.channel != self.policy.channel:
            raise UpdateBlocked("candidate channel no longer matches the enabled update policy")
        if candidate.source == "github_release":
            if self.policy.channel not in {"stable", "beta", "nightly"}:
                raise UpdateBlocked("release candidate cannot be used with a branch update policy")
            if _parse_version(candidate.version) is None:
                raise UpdateBlocked(f"candidate version is not a valid semantic version: {candidate.version!r}")
            expected_ref = f"refs/tags/{candidate.version}"
            if candidate.ref != expected_ref:
                raise UpdateBlocked("candidate release ref does not match its version")
            if candidate.release_url:
                remote = self._remote()
                if not _trusted_github_url(candidate.release_url, remote):
                    raise UpdateBlocked("candidate release URL is not the trusted GitHub repository")
        elif candidate.source == "github_branch":
            if self.policy.channel not in {"main", "development"}:
                raise UpdateBlocked("branch candidate cannot be used with a release update policy")
            expected_ref = f"refs/remotes/{self.policy.remote}/{self.policy.branch}"
            if candidate.ref != expected_ref:
                raise UpdateBlocked("candidate branch ref does not match the configured branch")
            if candidate.release_url:
                remote = self._remote()
                if not _trusted_github_url(candidate.release_url, remote, path_prefix="/commit/"):
                    raise UpdateBlocked("candidate branch URL is not the trusted GitHub repository")
        else:
            raise UpdateBlocked(f"unsupported update candidate source: {candidate.source!r}")
        if not (40 <= len(candidate.commit) <= 64) or any(ch not in "0123456789abcdef" for ch in candidate.commit):
            raise UpdateBlocked("candidate commit is not a full immutable Git object id")

    def _assert_deployment_boundary(self) -> None:
        mode = _deployment_mode()
        try:
            workers = int(os.getenv("GATEWAY_WORKERS", "1"))
        except (TypeError, ValueError):
            workers = 2
        if workers > 1:
            raise UpdateBlocked("in-place source updates require a single local Gateway worker")
        if not _deployment_can_self_update(mode, self.policy.restart_mode):
            raise UpdateBlocked(f"in-place source updates are disabled in {mode} deployments; update the image/chart/installer through its deployment orchestrator")

    def _deployment_status(self) -> dict[str, Any]:
        mode = _deployment_mode()
        can_self_update = _deployment_can_self_update(mode, self.policy.restart_mode)
        if mode != "local_source":
            reason = f"in-place source updates are disabled in {mode} deployments"
        elif not can_self_update:
            reason = "in-place source updates require a single local Gateway worker"
        else:
            reason = ""
        return {
            "deploymentMode": mode,
            "canSelfUpdate": can_self_update,
            "reason": reason,
        }

    def _installed_version(self) -> str:
        value = self.repo.current_version()
        if value != "unknown":
            return value
        return release_check.resolve_alpha_version()

    def _discover_candidate(self) -> UpdateCandidate | None:
        status = self._status()
        installed = self._installed_version()
        channel = self.policy.channel
        if channel in {"main", "development"}:
            branch = self.policy.branch
            if branch not in self.policy.allowed_branches:
                raise UpdateBlocked(f"branch {branch!r} is not in policy.allowed_branches")
            target = self.repo.fetch_branch(branch)
            if target == status.head:
                return None
            if not self.repo.is_ancestor(status.head, target):
                raise UpdateBlocked(f"remote branch {branch!r} is not a fast-forward of local HEAD; refusing to reset or merge")
            return UpdateCandidate(
                version=f"{installed}+git.{target[:12]}",
                ref=f"refs/remotes/{self.policy.remote}/{branch}",
                commit=target,
                channel=channel,
                source="github_branch",
                release_url=f"https://github.com/{self._remote().owner}/{self._remote().repository}/commit/{target}",
            )

        release = release_check._fetch_release_for_channel(
            self._remote().owner,
            self._remote().repository,
            channel,
        )
        if release.get("draft"):
            raise UpdateTransactionError("GitHub returned a draft release; refusing to install it")
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.strip():
            raise UpdateTransactionError("GitHub release response has no usable 'tag_name'")
        tag = tag.strip()
        if not re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", tag):
            raise UpdateTransactionError(f"published release tag {tag!r} is not a strict semantic-version tag")
        parsed_tag = _parse_version(tag)
        if release.get("prerelease") or (channel == "stable" and parsed_tag is not None and parsed_tag.is_prerelease):
            if channel == "stable":
                raise UpdateTransactionError("stable channel returned a prerelease; refusing channel downgrade")
        target = self.repo.fetch_release_tag(tag)
        if self.policy.verify_release_version:
            target_versions = self.repo.version_sources_at(target)
            if not target_versions:
                raise UpdateTransactionError("published release has no readable version manifest; refusing an unverifiable target")
            for source_path, source_version in target_versions.items():
                parsed_source = _parse_version(source_version)
                if parsed_tag is None or parsed_source is None or parsed_source != parsed_tag:
                    raise UpdateTransactionError(f"published tag {tag!r} disagrees with target version source {source_path}={source_version!r}")
        newer = _is_newer(tag, installed)
        if newer is None:
            raise UpdateTransactionError(f"Cannot compare release {tag!r} with installed version {installed!r}; refusing to guess")
        if not newer:
            return None
        minimum = release.get("minimum_version")
        if minimum is not None and not isinstance(minimum, str):
            minimum = None
        if minimum:
            minimum_comparison = _is_newer(minimum, installed)
            if minimum_comparison is None or minimum_comparison is True:
                raise UpdateBlocked(f"installed version {installed!r} does not satisfy release minimum {minimum!r}")
        release_url = release.get("html_url") if isinstance(release.get("html_url"), str) else None
        if release_url:
            remote = self._remote()
            if not _trusted_github_url(release_url, remote):
                raise UpdateTransactionError("GitHub release URL does not match the trusted repository")
        return UpdateCandidate(
            version=tag,
            ref=f"refs/tags/{tag}",
            commit=target,
            channel=channel,
            source="github_release",
            release_url=release_url,
            prerelease=bool(release.get("prerelease", False)),
            minimum_version=minimum,
        )

    def _recent_check(self, state: dict[str, Any]) -> bool:
        if state.get("state") in {release_check.DISABLED, release_check.CHECKING}:
            return False
        checked = state.get("checkedAt")
        if not isinstance(checked, str) or not checked:
            return False
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(checked)).total_seconds()
        except ValueError:
            return False
        return 0 <= age < self.policy.check_interval_seconds

    def check(self, *, force: bool = False, enabled_only: bool = False) -> dict[str, Any]:
        """Check for a candidate without changing the checkout.

        The method is deliberately non-throwing: an unavailable GitHub API,
        malformed release, or non-Git checkout becomes a persisted honest
        ``CHECK_FAILED`` state.  A real candidate that cannot yet be applied
        is ``UPDATE_AVAILABLE`` with ``canApply=false`` and a precise reason.
        """
        try:
            self._refresh_auto_policy()
        except UpdateBlocked as exc:
            return {**release_check._initial_update_state(), "state": release_check.CHECK_FAILED, "error": _safe_reason(exc), "reason": _safe_reason(exc), "canApply": False}
        if enabled_only and not self.policy.enabled:
            try:
                current = load_state()
            except UpdateStateError as exc:
                current = {**release_check._initial_update_state(), "state": release_check.CHECK_FAILED, "error": _safe_reason(exc)}
            if current.get("state") in release_check.IN_FLIGHT_STATES:
                return {**current, "checked": False, "reason": "auto-update policy is disabled; in-flight transaction preserved"}
            try:
                return self.state_writer.transition(
                    release_check.DISABLED,
                    checkedAt=_now_iso(),
                    canApply=False,
                    reason="auto-update policy is disabled",
                    error=None,
                )
            except UpdateStateError:
                return {**current, "state": release_check.DISABLED, "reason": "auto-update policy is disabled", "checked": False}

        try:
            current = load_state()
        except UpdateStateError as exc:
            # Never overwrite a corrupt state file with a fresh IDLE value. An
            # operator must inspect/recover it before a new transaction starts.
            return {
                **release_check._initial_update_state(),
                "state": release_check.CHECK_FAILED,
                "error": _safe_reason(exc),
                "reason": _safe_reason(exc),
                "canApply": False,
            }
        if current.get("state") in release_check.IN_FLIGHT_STATES:
            return current
        if not force and self._recent_check(current):
            return current

        try:
            with FileUpdateLock():
                writer = self.state_writer
                writer.transition(
                    release_check.CHECKING,
                    checkedAt=_now_iso(),
                    error=None,
                    reason=None,
                    previousCommit=current.get("currentCommit"),
                )
                candidate = self._discover_candidate()
                if candidate is None:
                    state = writer.transition(
                        release_check.UP_TO_DATE,
                        checkedAt=_now_iso(),
                        installedVersion=self._installed_version(),
                        currentCommit=self.repo.head(),
                        availableVersion=None,
                        targetCommit=None,
                        candidate=None,
                        source=None,
                        backupRef=None,
                        mutationStarted=False,
                        canApply=False,
                        deploymentMode=_deployment_mode(),
                        canSelfUpdate=_deployment_can_self_update(_deployment_mode(), self.policy.restart_mode),
                        reason="published source is already current",
                        error=None,
                    )
                    return state

                status = self.repo.status()
                deployment = self._deployment_status()
                can_apply = bool(deployment["canSelfUpdate"])
                reason = "validated candidate is ready to stage" if can_apply else deployment["reason"]
                if can_apply and self.policy.require_clean_worktree and not status.is_clean:
                    can_apply = False
                    reason = "worktree has tracked or untracked changes; commit/stash them before applying"
                if can_apply and status.branch not in self.policy.allowed_branches:
                    can_apply = False
                    reason = f"current branch {status.branch or 'detached'!r} is not allowed"
                if can_apply and self.policy.min_free_disk_mb > 0:
                    free_mb = self.repo.free_disk_bytes() // (1024 * 1024)
                    if free_mb < self.policy.min_free_disk_mb:
                        can_apply = False
                        reason = f"only {free_mb} MiB free; policy requires {self.policy.min_free_disk_mb} MiB"
                skipped = current.get("skippedVersions")
                if can_apply and _version_was_skipped(candidate.version, skipped):
                    can_apply = False
                    reason = f"version {candidate.version} was explicitly skipped"
                state = writer.transition(
                    release_check.UPDATE_AVAILABLE,
                    checkedAt=_now_iso(),
                    installedVersion=self._installed_version(),
                    currentCommit=status.head,
                    availableVersion=candidate.version,
                    targetCommit=candidate.commit,
                    source=candidate.source,
                    candidate=candidate.public_dict(),
                    canApply=can_apply,
                    deploymentMode=deployment["deploymentMode"],
                    canSelfUpdate=deployment["canSelfUpdate"],
                    reason=reason,
                    error=None,
                )
                return state
        except (UpdateBlocked,) as exc:
            try:
                return self.state_writer.transition(
                    release_check.BLOCKED,
                    checkedAt=_now_iso(),
                    canApply=False,
                    availableVersion=None,
                    targetCommit=None,
                    candidate=None,
                    source=None,
                    backupRef=None,
                    mutationStarted=False,
                    deploymentMode=_deployment_mode(),
                    canSelfUpdate=False,
                    reason=_safe_reason(exc),
                    error=None,
                )
            except UpdateStateError:
                logger.exception("Could not persist blocked update state")
                return {**current, "state": release_check.BLOCKED, "reason": _safe_reason(exc), "canApply": False}
        except (UpdateBusyError, UpdateStateError) as exc:
            logger.info("Update check deferred: %s", exc)
            return current
        except Exception as exc:  # honest boundary: no fabricated CHECK_FAILED
            reason = _safe_reason(exc)
            logger.warning("Update check failed: %s", reason)
            try:
                return self.state_writer.fail(
                    reason,
                    checkedAt=_now_iso(),
                    canApply=False,
                    availableVersion=None,
                    candidate=None,
                    source=None,
                    targetCommit=None,
                )
            except UpdateStateError:
                return {**current, "state": release_check.CHECK_FAILED, "error": reason, "checkedAt": _now_iso()}

    def skip_version(self, version: str) -> dict[str, Any]:
        """Persist an operator skip for one published version."""
        try:
            self._refresh_auto_policy()
        except UpdateBlocked as exc:
            return {"ok": False, "state": release_check.CHECK_FAILED, "reason": _safe_reason(exc)}
        if not self.policy.enabled:
            return {"ok": False, "state": release_check.DISABLED, "reason": "auto-update policy is disabled"}
        version = version.strip()
        if _parse_version(version) is None:
            return {"ok": False, "state": release_check.CHECK_FAILED, "reason": _safe_reason(f"skip version must be a semantic version, got {version!r}")}
        try:
            with FileUpdateLock():
                state = load_state()
                if state.get("state") in release_check.IN_FLIGHT_STATES:
                    return {"ok": False, "state": state.get("state"), "reason": "cannot skip while an update transaction is in flight"}
                skipped = state.get("skippedVersions")
                if not isinstance(skipped, list):
                    skipped = []
                if version not in skipped:
                    skipped.append(version)
                return {
                    "ok": True,
                    **self.state_writer.transition(
                        release_check.UP_TO_DATE,
                        skippedVersions=skipped[-100:],
                        availableVersion=None,
                        candidate=None,
                        source=None,
                        targetCommit=None,
                        canApply=False,
                        reason=f"version {version} was explicitly skipped",
                        error=None,
                    ),
                }
        except (UpdateBusyError, UpdateStateError) as exc:
            return {"ok": False, "state": release_check.CHECK_FAILED, "reason": _safe_reason(exc)}

    def check_and_maybe_apply(self) -> dict[str, Any]:
        """One autonomy-loop tick; auto-apply is a separate explicit policy bit."""
        try:
            self._refresh_auto_policy()
        except UpdateBlocked as exc:
            return {"state": release_check.CHECK_FAILED, "error": _safe_reason(exc), "reason": _safe_reason(exc)}
        if not self.policy.enabled:
            return {"state": release_check.DISABLED, "reason": "auto-update policy is disabled", "checked": False}
        result = self.check(enabled_only=True)
        if self.policy.auto_apply and result.get("state") == release_check.UPDATE_AVAILABLE and result.get("canApply") and int(result.get("failedAttempts", 0) or 0) < self.policy.startup_failure_threshold and self._is_idle():
            return self.request_apply(force=False)
        return result

    # -- detached apply -----------------------------------------------------

    def _helper_command(self, transaction_id: str, *, force: bool = False) -> list[str]:
        helper = self.root / "scripts" / "auto_update.py"
        command = [sys.executable]
        if helper.is_file():
            command.append(str(helper))
        else:
            command.extend(["-m", "alpha.evolution.update_cli"])
        command.extend(["apply", "--yes"])
        if force:
            command.append("--force")
        command.extend(["--transaction-id", transaction_id])
        return command

    def request_apply(self, *, force: bool = False) -> dict[str, Any]:
        """Queue a detached apply; never mutate the running Gateway process."""
        try:
            self._refresh_auto_policy()
        except UpdateBlocked as exc:
            return {"ok": False, "state": release_check.CHECK_FAILED, "reason": _safe_reason(exc)}
        try:
            state = load_state()
        except UpdateStateError as exc:
            return {"ok": False, "state": release_check.CHECK_FAILED, "reason": _safe_reason(exc)}
        if not self.policy.enabled:
            return {"ok": False, "state": release_check.DISABLED, "reason": "auto-update policy is disabled"}
        if maintenance_active():
            return {"ok": False, "state": release_check.APPLY_REQUESTED, "reason": "another source update currently owns the maintenance barrier"}
        try:
            self._assert_deployment_boundary()
        except UpdateBlocked as exc:
            return {"ok": False, "state": release_check.BLOCKED, "reason": _safe_reason(exc)}
        candidate = UpdateCandidate.from_public_dict(state.get("candidate"))
        if candidate is None:
            return {"ok": False, "state": state.get("state", release_check.IDLE), "reason": "no validated update candidate"}
        try:
            self._validate_candidate_shape(candidate)
        except (UpdateBlocked, UpdateTransactionError, GitRepositoryError) as exc:
            return {"ok": False, "state": release_check.BLOCKED, "reason": _safe_reason(exc)}
        if not self.policy.auto_apply and not force:
            return {"ok": False, "state": state.get("state", release_check.IDLE), "reason": "auto_apply is disabled; explicit operator confirmation is required"}
        # ``force`` only means an attended operator explicitly accepted the
        # manual handoff.  It must never bypass a server-side safety verdict.
        if not state.get("canApply"):
            return {"ok": False, "state": state.get("state", release_check.BLOCKED), "reason": state.get("reason") or "candidate is not applicable"}
        if state.get("state") in release_check.IN_FLIGHT_STATES:
            return {"ok": False, "state": state.get("state"), "reason": "an update transaction is already in flight"}
        if self.policy.respect_active_runs and not self._is_idle():
            return {"ok": False, "state": state.get("state"), "reason": "active runs are using the current checkout"}

        transaction_id = f"upd-{uuid.uuid4().hex[:16]}"
        log_handle = None
        try:
            # Re-check and claim the candidate under the same cross-process lock
            # used by checks/apply.  Two Gateway requests must not both spawn a
            # detached transaction from the same persisted UPDATE_AVAILABLE.
            with FileUpdateLock():
                latest = load_state()
                if latest.get("state") in release_check.IN_FLIGHT_STATES:
                    return {"ok": False, "state": latest.get("state"), "reason": "an update transaction is already in flight"}
                latest_candidate = UpdateCandidate.from_public_dict(latest.get("candidate"))
                if latest_candidate is None or latest_candidate.commit != candidate.commit or latest_candidate.ref != candidate.ref:
                    return {"ok": False, "state": latest.get("state", release_check.UPDATE_AVAILABLE), "reason": "the verified update candidate changed; check again"}
                if not latest.get("canApply"):
                    return {"ok": False, "state": latest.get("state", release_check.BLOCKED), "reason": latest.get("reason") or "candidate is not applicable"}
                self.state_writer.transition(
                    release_check.APPLY_REQUESTED,
                    transactionId=transaction_id,
                    candidate=candidate.public_dict(),
                    canApply=True,
                    reason="detached update transaction requested",
                    mutationStarted=False,
                    supervisorPid=os.getpid(),
                    supervisorStartedAt=_now_iso(),
                    error=None,
                )
            log_dir = runtime_home() / "updates"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "daemon.log"
            with suppress(OSError):
                if log_path.is_file() and log_path.stat().st_size > 5 * 1024 * 1024:
                    log_path.replace(log_path.with_suffix(".log.1"))
            log_handle = log_path.open("a", encoding="utf-8")
            env = os.environ.copy()
            pythonpath = [str(self.root / "backend"), str(self.root / "backend" / "packages" / "harness")]
            if env.get("PYTHONPATH"):
                pythonpath.append(env["PYTHONPATH"])
            env["PYTHONPATH"] = os.pathsep.join(pythonpath)
            env["ALPHA_UPDATE_TRANSACTION_ID"] = transaction_id
            kwargs: dict[str, Any] = {
                "cwd": str(self.root),
                "env": env,
                "stdin": subprocess.DEVNULL,
                "stdout": log_handle,
                "stderr": subprocess.STDOUT,
            }
            if platform.system().lower() == "windows":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(self._helper_command(transaction_id, force=force), **kwargs)
            log_handle.close()
            log_handle = None
            return {"ok": True, "state": release_check.APPLY_REQUESTED, "transaction_id": transaction_id, "reason": "detached transaction started"}
        except UpdateBusyError as exc:
            if log_handle is not None:
                with suppress(OSError):
                    log_handle.close()
            return {"ok": False, "state": release_check.APPLY_REQUESTED, "reason": _safe_reason(exc)}
        except (OSError, UpdateStateError, ValueError) as exc:
            if log_handle is not None:
                with suppress(OSError):
                    log_handle.close()
            reason = _safe_reason(exc)
            try:
                self.state_writer.fail(reason, transactionId=transaction_id, canApply=False)
            except UpdateStateError:
                logger.exception("Could not persist update request failure")
            return {"ok": False, "state": release_check.CHECK_FAILED, "reason": reason}

    # -- transaction -------------------------------------------------------

    def _wait_until_idle(self) -> bool:
        """Close the admission race, then wait briefly for existing work."""
        if not self.policy.respect_active_runs:
            return True
        deadline = time.monotonic() + self.policy.maintenance_drain_seconds
        while True:
            if self._is_idle():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))

    def _begin_maintenance(self, transaction_id: str) -> None:
        write_maintenance(transaction_id, phase=release_check.QUIESCING)

    @contextmanager
    def _maintenance_guard(self, transaction_id: str):
        if not self._wait_until_idle():
            raise UpdateBlocked("active runs did not drain before the update timeout")
        self._begin_maintenance(transaction_id)
        try:
            yield
        finally:
            self._end_maintenance(transaction_id)

    def _end_maintenance(self, transaction_id: str) -> None:
        clear_maintenance(transaction_id)

    def _restore_dependencies_after_rollback(self) -> None:
        """Re-synchronize declared dependencies at the restored commit."""
        if not self.policy.sync_dependencies or not self.policy.restore_dependencies_on_rollback:
            return
        uv = shutil.which("uv")
        if uv:
            self._command_runner([uv, "sync", "--locked", "--all-packages"], cwd=self.root / "backend", timeout_seconds=1_800)
        frontend_runner = self.root / "scripts" / "pnpm.py"
        if frontend_runner.is_file() and (self.root / "frontend" / "pnpm-lock.yaml").is_file():
            self._command_runner(
                [sys.executable, str(frontend_runner), "install", "--frozen-lockfile"],
                cwd=self.root / "frontend",
                timeout_seconds=1_800,
            )

    def _prune_transaction_snapshots(self, keep: int) -> None:
        base = runtime_home() / "updates" / "transactions"
        try:
            directories = [entry for entry in base.iterdir() if entry.is_dir()]
        except (FileNotFoundError, OSError):
            return
        directories.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
        for entry in directories[max(1, int(keep)) :]:
            shutil.rmtree(entry, ignore_errors=True)

    def _snapshot_configs(self, transaction_id: str) -> Path:
        target = runtime_home() / "updates" / "transactions" / transaction_id / "config"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("config.yaml", "extensions_config.json"):
            source = self.root / name
            if _copy_if_present(source, target / name):
                continue
            (target / f".{name}.absent").write_text("absent\n", encoding="utf-8")
        return target

    def _run_post_update_commands(self, old_commit: str, new_commit: str) -> None:
        changed = set(self.repo.changed_paths(old_commit, new_commit))
        if self.policy.run_config_upgrade:
            script = self.root / "scripts" / "config-upgrade.sh"
            shell = _find_bash()
            if not script.is_file() or not shell:
                raise UpdateTransactionError("config-upgrade hook is required but scripts/config-upgrade.sh or a POSIX shell is unavailable")
            self._command_runner([shell, str(script)], cwd=self.root, timeout_seconds=300)
        if not self.policy.sync_dependencies:
            return
        backend_locked = changed.intersection(
            {
                "backend/pyproject.toml",
                "backend/uv.lock",
                "backend/packages/harness/pyproject.toml",
            }
        )
        if backend_locked:
            uv = shutil.which("uv")
            if not uv:
                raise UpdateTransactionError("uv is required to synchronize changed backend dependencies")
            self._command_runner([uv, "sync", "--locked", "--all-packages"], cwd=self.root / "backend", timeout_seconds=1_800)
        frontend_locked = changed.intersection({"frontend/package.json", "frontend/pnpm-lock.yaml"})
        if frontend_locked:
            self._command_runner(
                [sys.executable, str(self.root / "scripts" / "pnpm.py"), "install", "--frozen-lockfile"],
                cwd=self.root / "frontend",
                timeout_seconds=1_800,
            )

    def _stop_services(self) -> None:
        if self._stop is not None:
            self._stop()
        elif self._restart is None:
            _default_stop(self.root, self.policy.restart_mode)

    def _start_services(self) -> None:
        if self._restart is not None:
            self._restart()
        else:
            _default_start(self.root, self.policy.restart_mode)

    def _restart_services(self) -> None:
        if self._restart is not None:
            self._restart()
        else:
            _default_restart(self.root, self.policy.restart_mode)

    def _health_ok(self) -> bool:
        if self._health_check is not None:
            return bool(self._health_check())
        if not self.policy.health_urls:
            return True
        for attempt in range(self.policy.health_check_attempts):
            if _default_health(self.policy.health_urls, timeout_seconds=self.policy.health_check_timeout_seconds):
                return True
            if attempt + 1 < self.policy.health_check_attempts:
                time.sleep(self.policy.health_check_interval_seconds)
        return False

    def _rollback(
        self,
        *,
        backup_ref: str,
        snapshot_dir: Path,
        transaction_id: str,
        reason: str,
        lock_held: bool = False,
    ) -> bool:
        if not lock_held:
            with FileUpdateLock():
                return self._rollback(
                    backup_ref=backup_ref,
                    snapshot_dir=snapshot_dir,
                    transaction_id=transaction_id,
                    reason=reason,
                    lock_held=True,
                )
        writer = self.state_writer
        backup_refs_getter = getattr(self.repo, "backup_refs", None)
        if callable(backup_refs_getter):
            backup_refs = set(backup_refs_getter())
            if backup_ref not in backup_refs:
                raise GitRepositoryError("backup ref is not present in the trusted Alpha backup namespace")
        state_before_rollback = load_state()
        expected_previous = state_before_rollback.get("previousCommit")
        resolver = getattr(self.repo, "resolve", None)
        if isinstance(expected_previous, str) and expected_previous and callable(resolver):
            if resolver(backup_ref) != expected_previous:
                raise GitRepositoryError("backup ref no longer points to the recorded previous commit")
        if not self.policy.rollback_on_failure:
            current = load_state()
            attempts = current.get("failedAttempts")
            if not isinstance(attempts, int) or attempts < 0:
                attempts = 0
            writer.transition(
                release_check.RECOVERY_REQUIRED,
                transactionId=transaction_id,
                backupRef=backup_ref,
                reason=reason,
                error=f"automatic rollback disabled after {reason}",
                rolledBack=False,
                mutationStarted=True,
                failedAttempts=attempts + 1,
            )
            return False
        current = load_state()
        attempts = current.get("failedAttempts")
        if not isinstance(attempts, int) or attempts < 0:
            attempts = 0
        writer.transition(
            release_check.ROLLBACK,
            transactionId=transaction_id,
            backupRef=backup_ref,
            reason=reason,
            error=reason,
            failedAttempts=attempts + 1,
        )
        try:
            self.repo.reset_hard(backup_ref)
            _restore_config_snapshot(snapshot_dir, self.root)
            self._restore_dependencies_after_rollback()
            writer.transition(
                release_check.RESTORE,
                transactionId=transaction_id,
                backupRef=backup_ref,
                currentCommit=self.repo.head(),
                reason="previous checkout restored",
            )
            self._restart_services()
            healthy = self._health_ok()
        except Exception as exc:  # rollback itself must be disclosed, never claimed healthy
            logger.exception("Automatic update rollback failed")
            writer.transition(
                release_check.RECOVERY_REQUIRED,
                transactionId=transaction_id,
                backupRef=backup_ref,
                reason=_safe_reason(f"rollback failed after {reason}: {exc}"),
                error=_safe_reason(f"rollback failed after {reason}: {exc}"),
                rolledBack=False,
                mutationStarted=True,
            )
            return False
        if not healthy:
            recovery_reason = _safe_reason(f"rollback restored the previous checkout but health still failed after {reason}")
            writer.transition(
                release_check.RECOVERY_REQUIRED,
                transactionId=transaction_id,
                backupRef=backup_ref,
                reason=recovery_reason,
                error=recovery_reason,
                rolledBack=True,
                mutationStarted=True,
            )
            return False
        writer.transition(
            release_check.FAILED_UPDATE_RECORDED,
            transactionId=transaction_id,
            backupRef=backup_ref,
            reason=reason,
            error=reason,
            rolledBack=True,
            mutationStarted=False,
            availableVersion=None,
            candidate=None,
            targetCommit=None,
        )
        self._prune_transaction_snapshots(self.policy.keep_backups)
        return True

    def apply_now(
        self,
        *,
        transaction_id: str | None = None,
        candidate: UpdateCandidate | None = None,
        force: bool = False,
    ) -> UpdateResult:
        """Apply a validated candidate synchronously (CLI/worker only)."""
        try:
            self._refresh_auto_policy()
        except UpdateBlocked as exc:
            return UpdateResult(False, release_check.CHECK_FAILED, _safe_reason(exc))
        if not self.policy.enabled:
            return UpdateResult(False, release_check.DISABLED, "auto-update policy is disabled")
        if maintenance_active():
            return UpdateResult(False, release_check.APPLY_REQUESTED, "another source update currently owns the maintenance barrier")
        if not self.policy.auto_apply and not force:
            return UpdateResult(False, release_check.UPDATE_AVAILABLE, "auto_apply is disabled; explicit operator confirmation is required")
        try:
            self._assert_deployment_boundary()
        except UpdateBlocked as exc:
            return UpdateResult(False, release_check.BLOCKED, _safe_reason(exc))
        try:
            state = load_state()
        except UpdateStateError as exc:
            return UpdateResult(False, release_check.CHECK_FAILED, _safe_reason(exc))
        candidate = candidate or UpdateCandidate.from_public_dict(state.get("candidate"))
        if candidate is None:
            return UpdateResult(False, state.get("state", release_check.IDLE), "no validated update candidate")
        try:
            self._validate_candidate_shape(candidate)
        except (UpdateBlocked, UpdateTransactionError, GitRepositoryError) as exc:
            return UpdateResult(False, release_check.BLOCKED, _safe_reason(exc))
        persisted_candidate = UpdateCandidate.from_public_dict(state.get("candidate"))
        if persisted_candidate is None:
            return UpdateResult(False, release_check.BLOCKED, "no server-persisted update candidate")
        if persisted_candidate.commit != candidate.commit or persisted_candidate.ref != candidate.ref or persisted_candidate.version != candidate.version:
            return UpdateResult(False, release_check.BLOCKED, "apply candidate does not match the server-persisted candidate")
        if not state.get("canApply"):
            return UpdateResult(False, state.get("state", release_check.BLOCKED), _safe_reason(state.get("reason") or "candidate is not applicable"))
        if state.get("state") in release_check.IN_FLIGHT_STATES and transaction_id and state.get("transactionId") not in {None, transaction_id}:
            return UpdateResult(False, state.get("state", release_check.APPLY_REQUESTED), "an update transaction is already in flight")
        if state.get("state") in release_check.IN_FLIGHT_STATES and not transaction_id and state.get("state") != release_check.APPLY_REQUESTED:
            return UpdateResult(False, state.get("state", release_check.APPLY_REQUESTED), "an update transaction is already in flight")
        if self.policy.respect_active_runs and not self._is_idle():
            return UpdateResult(False, state.get("state", release_check.UPDATE_AVAILABLE), "active runs are using the current checkout")
        transaction_id = transaction_id or (state.get("transactionId") if isinstance(state.get("transactionId"), str) and state.get("transactionId") else f"upd-{uuid.uuid4().hex[:16]}")
        if not _valid_transaction_id(transaction_id):
            return UpdateResult(False, release_check.CHECK_FAILED, "invalid transaction id", transaction_id)
        persisted_transaction_id = state.get("transactionId")
        if persisted_transaction_id and persisted_transaction_id != transaction_id:
            return UpdateResult(False, release_check.CHECK_FAILED, "transaction id does not match the persisted update request", transaction_id)
        old_commit = self.repo.head()
        target_commit = candidate.commit
        backup_ref: str | None = None
        snapshot_dir: Path | None = None
        maintenance_owned = False
        try:
            if not self._wait_until_idle():
                raise UpdateBlocked("active runs did not drain before the update timeout")
            self._begin_maintenance(transaction_id)
            maintenance_owned = True
            if not self._wait_until_idle():
                raise UpdateBlocked("active runs appeared while the update admission barrier was being published")
            with FileUpdateLock():
                writer = self.state_writer
                writer.transition(
                    release_check.QUIESCING,
                    transactionId=transaction_id,
                    reason="new run admission is paused while the update transaction starts",
                    error=None,
                )
                writer.transition(
                    release_check.STAGING,
                    transactionId=transaction_id,
                    candidate=candidate.public_dict(),
                    previousCommit=old_commit,
                    targetCommit=target_commit,
                    currentCommit=old_commit,
                    supervisorPid=os.getpid(),
                    supervisorStartedAt=_now_iso(),
                    canApply=True,
                    reason="staging Git object and configuration backups",
                    error=None,
                )
                checkout = self.repo.validate_checkout(expected_remote=self._remote())
                if checkout.head != old_commit:
                    raise UpdateBlocked("checkout HEAD changed before staging; retry the update check")
                if self.policy.require_clean_worktree and not checkout.is_clean:
                    raise UpdateBlocked("worktree became dirty before staging")
                if checkout.branch not in self.policy.allowed_branches:
                    raise UpdateBlocked(f"current branch {checkout.branch or 'detached'!r} is not allowed")
                if self.repo.resolve(candidate.ref) != target_commit:
                    raise UpdateTransactionError("candidate commit no longer matches its Git ref; refusing a moving target")
                if not self.repo.is_ancestor(old_commit, target_commit):
                    raise UpdateBlocked("target is not a fast-forward of the current checkout")
                if self.policy.verify_signed_commit:
                    self.repo.verify_commit(target_commit)
                snapshot_dir = self._snapshot_configs(transaction_id)
                backup_ref = self.repo.create_backup_ref(transaction_id)
                writer.transition(
                    release_check.BACKUP_CREATED,
                    transactionId=transaction_id,
                    backupRef=backup_ref,
                    reason="Git backup ref and config snapshot created",
                    error=None,
                )
                # Fetch is repeated immediately before switching so a release
                # tag/branch cannot move between discovery and installation.
                writer.transition(release_check.DOWNLOADING, transactionId=transaction_id, reason="fetching immutable target from trusted remote")
                if candidate.source == "github_release":
                    fetched = self.repo.fetch_release_tag(candidate.version)
                else:
                    fetched = self.repo.fetch_branch(self.policy.branch)
                if fetched != target_commit:
                    raise UpdateTransactionError("remote target changed after discovery; refusing to install a moving target")
                writer.transition(release_check.DOWNLOADED, transactionId=transaction_id, reason="target commit fetched")
                writer.transition(release_check.VERIFYING, transactionId=transaction_id, reason="validating target ancestry and integrity", mutationStarted=False)
                if self.policy.verify_signed_commit:
                    self.repo.verify_commit(target_commit)
                # Persist the mutation boundary before the first checkout write.
                # Recovery can therefore distinguish a crash during fetch/verify
                # (safe to abandon) from one that may need a reset.
                self._refresh_auto_policy()
                if not self.policy.enabled:
                    raise UpdateBlocked("auto-update policy was disabled before checkout mutation")
                if self.policy.min_free_disk_mb > 0:
                    free_mb = self.repo.free_disk_bytes() // (1024 * 1024)
                    if free_mb < self.policy.min_free_disk_mb:
                        raise UpdateBlocked(f"only {free_mb} MiB free after fetch; policy requires {self.policy.min_free_disk_mb} MiB")
                if not self._is_idle():
                    raise UpdateBlocked("active runs appeared before checkout mutation")
                writer.transition(release_check.STOPPING, transactionId=transaction_id, reason="stopping local services before checkout mutation")
                self._stop_services()
                writer.transition(release_check.INSTALLING, transactionId=transaction_id, reason="fast-forward boundary reached", mutationStarted=True)
                self.repo.fast_forward(target_commit)
                writer.transition(release_check.INSTALLING, transactionId=transaction_id, reason="fast-forward applied; running post-update hooks")
                self._run_post_update_commands(old_commit, target_commit)
                writer.transition(release_check.READY_TO_SWITCH, transactionId=transaction_id, reason="post-update hooks completed")
                writer.transition(release_check.RESTARTING, transactionId=transaction_id, reason="starting local services")
                self._start_services()
                writer.transition(release_check.HEALTH_CHECK, transactionId=transaction_id, reason="checking Gateway and frontend readiness")
                if not self._health_ok():
                    raise UpdateTransactionError("post-update health check failed")
                state_after = writer.transition(
                    release_check.HEALTHY,
                    transactionId=transaction_id,
                    currentCommit=target_commit,
                    availableVersion=None,
                    candidate=None,
                    canApply=False,
                    reason="update verified healthy",
                    error=None,
                    failedAttempts=0,
                    mutationStarted=False,
                    lastAppliedAt=_now_iso(),
                    lastSuccessfulAt=_now_iso(),
                )
                try:
                    self.repo.prune_backups(self.policy.keep_backups)
                except (GitCommandError, GitRepositoryError) as exc:
                    logger.warning("Could not prune Alpha update backup refs: %s", exc)
                self._prune_transaction_snapshots(self.policy.keep_backups)
                return UpdateResult(True, state_after["state"], transaction_id=transaction_id, from_commit=old_commit, to_commit=target_commit)
        except (UpdateBlocked, UpdateBusyError, UpdateStateError, GitCommandError, GitRepositoryError, UpdateTransactionError) as exc:
            reason = _safe_reason(exc)
            logger.warning("Update transaction %s failed: %s", transaction_id, reason)
            if isinstance(exc, UpdateBlocked) and not backup_ref:
                try:
                    self.state_writer.transition(
                        release_check.BLOCKED,
                        transactionId=transaction_id,
                        canApply=False,
                        reason=reason,
                        error=None,
                        mutationStarted=False,
                    )
                except UpdateStateError:
                    logger.exception("Could not persist blocked update state")
                return UpdateResult(False, release_check.BLOCKED, reason, transaction_id, old_commit, target_commit, False)
            if backup_ref and snapshot_dir is not None:
                try:
                    with FileUpdateLock():
                        rolled_back = self._rollback(
                            backup_ref=backup_ref,
                            snapshot_dir=snapshot_dir,
                            transaction_id=transaction_id,
                            reason=reason,
                            lock_held=True,
                        )
                except (UpdateBusyError, UpdateStateError):
                    rolled_back = False
                return UpdateResult(False, release_check.FAILED_UPDATE_RECORDED, reason, transaction_id, old_commit, target_commit, rolled_back)
            try:
                self.state_writer.fail(reason, transactionId=transaction_id, rolledBack=False)
            except UpdateStateError:
                logger.exception("Could not persist update failure")
            return UpdateResult(False, release_check.FAILED_UPDATE_RECORDED, reason, transaction_id, old_commit, target_commit, False)
        except Exception as exc:  # defensive boundary; never report success
            reason = _safe_reason(f"{type(exc).__name__}: {exc}")
            logger.exception("Unexpected update transaction failure")
            if backup_ref and snapshot_dir is not None:
                try:
                    with FileUpdateLock():
                        rolled_back = self._rollback(
                            backup_ref=backup_ref,
                            snapshot_dir=snapshot_dir,
                            transaction_id=transaction_id,
                            reason=reason,
                            lock_held=True,
                        )
                except (UpdateBusyError, UpdateStateError):
                    rolled_back = False
                return UpdateResult(False, release_check.FAILED_UPDATE_RECORDED, reason, transaction_id, old_commit, target_commit, rolled_back)
            return UpdateResult(False, release_check.FAILED_UPDATE_RECORDED, reason, transaction_id, old_commit, target_commit, False)
        finally:
            if maintenance_owned:
                self._end_maintenance(transaction_id)

    def recover_incomplete(self) -> UpdateResult:
        """Recover an interrupted transaction without guessing its phase.

        A crash before the first checkout write has nothing to restore and is
        safely closed as a recorded failed request.  Once ``mutationStarted``
        is durable, recovery uses only the exact backup ref and then performs
        the same restart/health verification as an automatic rollback.
        """
        try:
            self._refresh_auto_policy()
        except UpdateBlocked as exc:
            return UpdateResult(False, release_check.CHECK_FAILED, _safe_reason(exc))
        if not self.policy.enabled:
            return UpdateResult(False, release_check.DISABLED, "auto-update policy is disabled; re-enable it for an explicit recovery")
        try:
            state = load_state()
        except UpdateStateError as exc:
            return UpdateResult(False, release_check.CHECK_FAILED, _safe_reason(exc))
        if state.get("state") not in release_check.IN_FLIGHT_STATES:
            if maintenance_active():
                clear_maintenance(state.get("transactionId") if isinstance(state.get("transactionId"), str) else None)
            return UpdateResult(True, str(state.get("state", release_check.IDLE)), "no incomplete transaction")
        raw_transaction_id = state.get("transactionId")
        if raw_transaction_id and not _valid_transaction_id(raw_transaction_id):
            return UpdateResult(False, release_check.CHECK_FAILED, "persisted update transaction id is unsafe", None)
        transaction_id = raw_transaction_id or f"recover-{uuid.uuid4().hex[:12]}"
        phase = state.get("state")
        mutation_started = bool(state.get("mutationStarted"))
        if phase in {
            release_check.INSTALLING,
            release_check.READY_TO_SWITCH,
            release_check.RESTARTING,
            release_check.HEALTH_CHECK,
            release_check.ROLLBACK,
            release_check.RESTORE,
            release_check.RECOVERY_REQUIRED,
        }:
            mutation_started = True
        backup_ref = state.get("backupRef")
        if not mutation_started:
            maintenance_owned = False
            try:
                # A crash during STOPPING can leave services down even though
                # Git was never changed.  Restart the old stack before closing
                # the transaction; otherwise recovery would report success while
                # leaving the local deployment unavailable.
                if phase == release_check.STOPPING:
                    if not self._wait_until_idle():
                        raise UpdateBlocked("active runs did not drain before recovery")
                    self._begin_maintenance(transaction_id)
                    maintenance_owned = True
                    self._restart_services()
                    if not self._health_ok():
                        raise UpdateTransactionError("could not restart services after an interrupted pre-mutation stop")
                with FileUpdateLock():
                    self.state_writer.transition(
                        release_check.FAILED_UPDATE_RECORDED,
                        transactionId=transaction_id,
                        backupRef=backup_ref if isinstance(backup_ref, str) else None,
                        reason="interrupted before checkout mutation; no restore was required",
                        error="interrupted before checkout mutation",
                        rolledBack=False,
                        mutationStarted=False,
                        candidate=None,
                        availableVersion=None,
                        targetCommit=None,
                        canApply=False,
                    )
                return UpdateResult(True, release_check.FAILED_UPDATE_RECORDED, "interrupted before mutation; no restore was required", transaction_id)
            except (UpdateBlocked, UpdateBusyError, UpdateStateError, UpdateTransactionError) as exc:
                return UpdateResult(False, release_check.CHECK_FAILED, _safe_reason(exc), transaction_id)
            finally:
                if maintenance_owned:
                    self._end_maintenance(transaction_id)
        if not isinstance(backup_ref, str) or not backup_ref.startswith("refs/alpha-update/backups/"):
            return UpdateResult(False, release_check.FAILED_UPDATE_RECORDED, "in-flight state has no safe backup ref", transaction_id)
        snapshot = runtime_home() / "updates" / "transactions" / transaction_id / "config"
        maintenance_owned = False
        try:
            if not self._wait_until_idle():
                raise UpdateBlocked("active runs did not drain before recovery")
            self._begin_maintenance(transaction_id)
            maintenance_owned = True
            if not self._wait_until_idle():
                raise UpdateBlocked("active runs appeared while the update admission barrier was being published")
            with FileUpdateLock():
                ok = self._rollback(
                    backup_ref=backup_ref,
                    snapshot_dir=snapshot,
                    transaction_id=transaction_id,
                    reason="recovering an interrupted update transaction",
                    lock_held=True,
                )
            return UpdateResult(
                ok,
                release_check.FAILED_UPDATE_RECORDED if ok else release_check.RECOVERY_REQUIRED,
                "recovery completed" if ok else "recovery health check failed",
                transaction_id,
                rolled_back=ok,
            )
        except (UpdateBlocked, UpdateBusyError, GitCommandError, GitRepositoryError, UpdateTransactionError, UpdateStateError) as exc:
            return UpdateResult(False, release_check.CHECK_FAILED, _safe_reason(exc), transaction_id)
        finally:
            if maintenance_owned:
                self._end_maintenance(transaction_id)

    def status(self) -> dict[str, Any]:
        """Return policy, persisted state, and a bounded audit tail."""
        try:
            self._refresh_auto_policy()
        except UpdateBlocked as exc:
            logger.warning("Could not refresh update policy for status: %s", exc)
        try:
            state = load_state()
        except UpdateStateError as exc:
            state = {**release_check._initial_update_state(), "state": release_check.CHECK_FAILED, "error": _safe_reason(exc)}
        return {
            "state": safe_state_snapshot(state),
            "policy": self.policy.public_dict(),
            "history": safe_state_snapshot(history(limit=50)),
            "repository_root": str(self.root),
            **self._deployment_status(),
        }


_engine: UpdateEngine | None = None
_engine_lock = __import__("threading").Lock()


def get_update_engine() -> UpdateEngine:
    """Return the process-local engine singleton."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = UpdateEngine()
        return _engine


def reset_update_engine() -> None:
    """Test/operator seam for policy or checkout changes in one process."""
    global _engine
    with _engine_lock:
        _engine = None


__all__ = [
    "UpdateBlocked",
    "UpdateCandidate",
    "UpdateEngine",
    "UpdateResult",
    "UpdateTransactionError",
    "get_update_engine",
    "reset_update_engine",
]
