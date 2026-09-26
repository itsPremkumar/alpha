"""Narrow, argv-only Git adapter used by the Alpha update engine.

The updater never runs ``git`` through a shell and never accepts a ref,
branch, or remote from an HTTP request.  Callers pass values from the signed/
trusted project policy or from a validated GitHub release, and this module
still applies a second shape check before constructing a command.

This adapter is intentionally about a *source checkout*.  Container images,
Helm releases, and the Electron installer remain owned by their deployment
orchestrators; an in-place source update is not a valid substitute for those
release mechanisms.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+-]{0,199}$")


class GitCommandError(RuntimeError):
    """A bounded, non-secret error from a Git subprocess."""

    def __init__(self, args: list[str], returncode: int, stderr: str = "") -> None:
        self.args_used = tuple(args)
        self.returncode = returncode
        detail = (stderr or "").strip().replace("\x00", "")
        if len(detail) > 1_200:
            detail = detail[-1_200:]
        super().__init__(f"git {' '.join(args)} failed with exit {returncode}: {detail or 'no stderr'}")


class GitRepositoryError(RuntimeError):
    """The checkout is not safe or suitable for an update transaction."""


@dataclass(frozen=True, slots=True)
class GitStatus:
    """A point-in-time source checkout status."""

    root: Path
    branch: str
    head: str
    status_lines: tuple[str, ...]
    remote_url: str | None

    @property
    def is_clean(self) -> bool:
        return not self.status_lines

    @property
    def is_detached(self) -> bool:
        return not self.branch


@dataclass(frozen=True, slots=True)
class GitRemote:
    """A validated remote repository identity."""

    name: str
    url: str
    owner: str
    repository: str

    @property
    def canonical_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repository}"


def _safe_ref(value: str, field_name: str = "ref") -> str:
    value = value.strip()
    if not value or not _REF_RE.fullmatch(value) or value.startswith("-"):
        raise GitRepositoryError(f"Unsafe Git {field_name}: {value!r}")
    return value


def _safe_remote_name(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value) or value.startswith("-"):
        raise GitRepositoryError(f"Unsafe Git remote name: {value!r}")
    return value


def _safe_branch(value: str) -> str:
    value = _safe_ref(value, "branch")
    if value.endswith("/") or ".." in value or "@{" in value:
        raise GitRepositoryError(f"Unsafe Git branch: {value!r}")
    return value


def _normalise_repo_path(url: str) -> tuple[str, str] | None:
    """Return (host, owner/repo) for supported GitHub URL forms.

    The updater is intentionally stricter than Git's URL parser: only HTTPS
    and SSH GitHub remotes are accepted, and credential-bearing/query-bearing
    URLs are rejected before they can reach a subprocess or an error message.
    """
    text = url.strip()
    if not text:
        return None
    # SCP-style SSH is the only shorthand with an implicit ``git`` user.
    if text.startswith("git@github.com:"):
        path = text.split(":", 1)[1]
        return "github.com", path.removesuffix(".git")
    parsed = urlsplit(text)
    if parsed.scheme not in {"https", "ssh"} or not parsed.hostname:
        return None
    if parsed.query or parsed.fragment:
        return None
    # ``ssh://git@github.com/...`` is valid, but passwords and HTTPS userinfo
    # are never accepted.  A username without a password is harmless for SSH.
    if parsed.password or (parsed.scheme == "https" and parsed.username):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and (parsed.scheme, port) not in {("https", 443), ("ssh", 22)}:
        return None
    host = parsed.hostname.lower()
    path = parsed.path.strip("/").removesuffix(".git")
    if path.count("/") != 1:
        return None
    return host, path


def canonical_remote(name: str, url: str) -> GitRemote:
    """Validate and canonicalise a GitHub remote URL."""
    name = _safe_remote_name(name)
    parsed = _normalise_repo_path(url)
    if parsed is None:
        raise GitRepositoryError(f"Remote {name!r} is not a supported GitHub repository URL")
    host, path = parsed
    if host != "github.com":
        raise GitRepositoryError(f"Remote {name!r} must point to github.com, got {host!r}")
    owner, repository = path.split("/", 1)
    if not owner or not repository or not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repository):
        raise GitRepositoryError(f"Remote {name!r} has an invalid owner/repository path")
    return GitRemote(name=name, url=url, owner=owner, repository=repository)


class GitRepository:
    """Safe operations against one local source checkout."""

    def __init__(
        self,
        root: str | Path,
        *,
        remote: str = "origin",
        timeout_seconds: float = 60.0,
        git_binary: str = "git",
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.remote = _safe_remote_name(remote)
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.git_binary = git_binary

    @classmethod
    def available(cls, git_binary: str = "git") -> bool:
        return shutil.which(git_binary) is not None

    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = [self.git_binary, "-C", str(self.root), *args]
        try:
            result = subprocess.run(
                command,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitRepositoryError(f"Git executable {self.git_binary!r} is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitCommandError(args, 124, f"command timed out after {self.timeout_seconds:.0f}s") from exc
        if check and result.returncode != 0:
            raise GitCommandError(args, result.returncode, result.stderr or result.stdout)
        return result

    def validate_checkout(self, *, expected_remote: GitRemote | None = None) -> GitStatus:
        """Validate the repository root, branch, and optional remote identity."""
        if not self.root.is_dir():
            raise GitRepositoryError(f"Source checkout does not exist: {self.root}")
        top = self._run(["rev-parse", "--show-toplevel"]).stdout.strip()
        try:
            top_path = Path(top).resolve()
        except OSError as exc:
            raise GitRepositoryError(f"Git returned an unreadable repository root: {top!r}") from exc
        if top_path != self.root:
            raise GitRepositoryError(f"Updater root {self.root} is not the Git worktree root {top_path}")
        branch = self._run(["symbolic-ref", "--quiet", "--short", "HEAD"], check=False).stdout.strip()
        head = self.head()
        remote_url: str | None
        try:
            remote_url = self._run(["remote", "get-url", self.remote]).stdout.strip() or None
        except GitCommandError:
            remote_url = None
        if expected_remote is not None:
            if remote_url is None:
                raise GitRepositoryError(f"Git remote {self.remote!r} is not configured")
            actual = canonical_remote(self.remote, remote_url)
            if (actual.owner.lower(), actual.repository.lower()) != (
                expected_remote.owner.lower(),
                expected_remote.repository.lower(),
            ):
                raise GitRepositoryError(f"Git remote {self.remote!r} points to {actual.owner}/{actual.repository}, expected {expected_remote.owner}/{expected_remote.repository}")
        return GitStatus(root=self.root, branch=branch, head=head, status_lines=self.status_lines(), remote_url=remote_url)

    def head(self) -> str:
        value = self._run(["rev-parse", "HEAD"]).stdout.strip().lower()
        if not _SHA_RE.fullmatch(value):
            raise GitRepositoryError(f"Git returned an invalid HEAD: {value!r}")
        return value

    def branch_name(self) -> str:
        return self._run(["symbolic-ref", "--quiet", "--short", "HEAD"], check=False).stdout.strip()

    def status_lines(self) -> tuple[str, ...]:
        output = self._run(["status", "--porcelain=v1", "--untracked-files=all"]).stdout
        return tuple(line for line in output.splitlines() if line.strip())

    def status(self) -> GitStatus:
        return GitStatus(
            root=self.root,
            branch=self.branch_name(),
            head=self.head(),
            status_lines=self.status_lines(),
            remote_url=(self._run(["remote", "get-url", self.remote], check=False).stdout.strip() or None),
        )

    def remote_identity(self) -> GitRemote:
        url = self._run(["remote", "get-url", self.remote]).stdout.strip()
        return canonical_remote(self.remote, url)

    def current_version(self) -> str:
        """Read the installed harness version from the checkout manifest.

        The package metadata installed in a virtual environment can lag a
        source checkout during development, so the source manifest is the
        update engine's primary version source.  The backend manifest is a
        compatibility fallback for older checkouts.
        """
        candidates = (
            self.root / "backend" / "packages" / "harness" / "pyproject.toml",
            self.root / "backend" / "pyproject.toml",
        )
        for path in candidates:
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
                continue
            version = data.get("project", {}).get("version")
            if isinstance(version, str) and version.strip():
                return version.strip()
        return "unknown"

    def version_sources_at(self, ref: str) -> dict[str, str]:
        """Read every available release version source at ``ref``.

        Missing optional source files are omitted; a present source must be
        syntactically valid.  Keeping the paths explicit avoids executing a
        target checkout's package/configuration code merely to discover its
        version.
        """
        ref = _safe_ref(ref)
        sources: dict[str, str] = {}
        for relative in (
            "backend/packages/harness/pyproject.toml",
            "backend/pyproject.toml",
        ):
            result = self._run(["show", f"{ref}:{relative}"], check=False)
            if result.returncode != 0:
                continue
            try:
                data = tomllib.loads(result.stdout)
            except tomllib.TOMLDecodeError as exc:
                raise GitRepositoryError(f"target version manifest {relative} is invalid: {exc}") from exc
            version = data.get("project", {}).get("version")
            if not isinstance(version, str) or not version.strip():
                raise GitRepositoryError(f"target version manifest {relative} has no version")
            sources[relative] = version.strip()
        frontend = self._run(["show", f"{ref}:frontend/package.json"], check=False)
        if frontend.returncode == 0:
            try:
                data = json.loads(frontend.stdout)
            except json.JSONDecodeError as exc:
                raise GitRepositoryError(f"target version manifest frontend/package.json is invalid: {exc}") from exc
            version = data.get("version") if isinstance(data, dict) else None
            if not isinstance(version, str) or not version.strip():
                raise GitRepositoryError("target version manifest frontend/package.json has no version")
            sources["frontend/package.json"] = version.strip()
        chart = self._run(["show", f"{ref}:deploy/helm/agent-workspace/Chart.yaml"], check=False)
        if chart.returncode == 0:
            versions = re.findall(r"^(?:version|appVersion):\s*[\"']?([^\"'\s]+)", chart.stdout, flags=re.MULTILINE)
            if len(versions) < 2:
                raise GitRepositoryError("target Helm Chart.yaml does not declare both version and appVersion")
            sources["deploy/helm/agent-workspace/Chart.yaml:version"] = versions[0]
            sources["deploy/helm/agent-workspace/Chart.yaml:appVersion"] = versions[1]
        return sources

    def version_at(self, ref: str) -> str:
        """Read the primary source version manifest at an immutable ref."""
        sources = self.version_sources_at(ref)
        return next(iter(sources.values()), "unknown")

    def resolve(self, ref: str) -> str:
        ref = _safe_ref(ref)
        result = self._run(["rev-parse", "--verify", f"{ref}^{{commit}}"], check=False)
        value = result.stdout.strip().lower()
        if result.returncode != 0 or not _SHA_RE.fullmatch(value):
            detail = (result.stderr or result.stdout).strip()
            raise GitRepositoryError(f"Git ref {ref!r} does not resolve to a commit: {detail[:500]}")
        return value

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        ancestor = self.resolve(ancestor)
        descendant = self.resolve(descendant)
        result = self._run(["merge-base", "--is-ancestor", ancestor, descendant], check=False)
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise GitCommandError(["merge-base", "--is-ancestor"], result.returncode, result.stderr or result.stdout)

    def fetch_release_tag(self, tag: str) -> str:
        tag = _safe_ref(tag, "release tag")
        if tag.startswith("refs/tags/"):
            tag = tag.removeprefix("refs/tags/")
            tag = _safe_ref(tag, "release tag")
        # Fetch the named tag explicitly.  No shell is involved and the remote
        # has already passed canonical GitHub identity validation.
        self._run(["fetch", "--force", self.remote, "tag", tag])
        return self.resolve(f"refs/tags/{tag}")

    def fetch_branch(self, branch: str) -> str:
        branch = _safe_branch(branch)
        destination = f"refs/remotes/{self.remote}/{branch}"
        self._run(["fetch", "--force", self.remote, f"refs/heads/{branch}:{destination}"])
        return self.resolve(destination)

    def remote_head(self, branch: str) -> str:
        branch = _safe_branch(branch)
        result = self._run(["ls-remote", self.remote, f"refs/heads/{branch}"], check=False)
        if result.returncode != 0:
            raise GitCommandError(["ls-remote", self.remote, f"refs/heads/{branch}"], result.returncode, result.stderr or result.stdout)
        first = result.stdout.split()
        if not first or not _SHA_RE.fullmatch(first[0]):
            raise GitRepositoryError(f"Remote branch {branch!r} did not return a commit")
        return first[0].lower()

    def create_backup_ref(self, label: str | None = None) -> str:
        head = self.head()
        safe_label = re.sub(r"[^A-Za-z0-9._-]+", "-", label or "manual").strip("-")[:48] or "manual"
        stamp = f"{time.time_ns():020d}-{os.getpid()}-{safe_label}-{head[:12]}"
        ref = f"refs/alpha-update/backups/{stamp}"
        self._run(["update-ref", ref, head])
        return ref

    def fast_forward(self, target: str) -> None:
        target = self.resolve(target)
        self._run(["merge", "--ff-only", "--no-edit", target])

    def reset_hard(self, ref: str) -> None:
        ref = self.resolve(ref)
        self._run(["reset", "--hard", ref])

    def verify_commit(self, commit: str) -> None:
        commit = self.resolve(commit)
        result = self._run(["verify-commit", "--quiet", commit], check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or "signature verification failed"
            raise GitRepositoryError(f"Commit {commit[:12]} is not verifiably signed: {detail[:500]}")

    def changed_paths(self, old: str, new: str) -> tuple[str, ...]:
        old = self.resolve(old)
        new = self.resolve(new)
        output = self._run(["diff", "--name-only", old, new]).stdout
        return tuple(line.strip() for line in output.splitlines() if line.strip())

    def free_disk_bytes(self) -> int:
        return shutil.disk_usage(self.root).free

    def backup_refs(self) -> tuple[str, ...]:
        output = self._run(["for-each-ref", "--sort=-refname", "--format=%(refname)", "refs/alpha-update/backups/"]).stdout
        return tuple(line.strip() for line in output.splitlines() if line.strip())

    def prune_backups(self, keep: int) -> tuple[str, ...]:
        keep = max(1, int(keep))
        refs = self.backup_refs()
        removed: list[str] = []
        for ref in refs[:-keep] if len(refs) > keep else ():
            self._run(["update-ref", "-d", ref])
            removed.append(ref)
        return tuple(removed)


__all__ = [
    "GitCommandError",
    "GitRemote",
    "GitRepository",
    "GitRepositoryError",
    "GitStatus",
    "canonical_remote",
]
