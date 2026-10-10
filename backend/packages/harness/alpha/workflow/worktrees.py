"""Per-task git worktree claims: one task, one isolated checkout.

Why this exists
---------------
A workflow that fans work out across nodes editing the same repository has a
collision the runtime could not see: two nodes running concurrently (or two
runs of the same workflow) would read and write the same working tree, and the
engine's ``write_scope`` partitioning — which is about run state, not
filesystem paths — says nothing about it. The documented gap was "per-task git
worktree isolation is not wired into the workflow plane".

This module wires it, as a claim store plus real ``git worktree`` operations:

* **A claim is exclusive.** One ACTIVE claim per worktree path, enforced by the
  store at claim time. A second task asking for the same path is refused with
  the holder's identity named — the same one-shot honest refusal a lease
  gives, never a retry loop and never a silent share.
* **The path is confined.** Worktrees are created under the engine's declared
  worktree root with a deterministic, sanitized name per ``(run, node)``. A
  node cannot name a path; it names a task, and the root decides where the
  checkout lands.
* **The repo roots are a host allowlist.** ``repo_root`` arrives through node
  config, which is client-supplied input (``POST /api/workflows`` takes
  ``body.graph`` verbatim), so a host must bind the roots it permits. With no
  binding the node fails honestly rather than running ``git worktree add``
  against whatever path a request named.
* **Removal is measured, never assumed.** A removal that fails leaves the
  claim ACTIVE with the real error recorded; nothing reports a worktree as
  gone while it still occupies disk.

Honesty rules
-------------
* This is real subprocess work with real side effects, so every call is
  argv-only (no shell), bounded by a timeout, and the store is the same
  single-Gateway atomically-replaced JSON file as its neighbours: atomic and
  restart-recoverable for ONE Gateway process, never cross-process
  coordination.
* A claim records the head commit it was created at, so "what did this task
  actually see" is answerable after the fact.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "DEFAULT_GIT_TIMEOUT_SECONDS",
    "ProvisionOutcome",
    "WorktreeClaim",
    "WorktreeStatus",
    "WorktreeStore",
    "WorktreeStoreError",
    "confined_worktree_path",
    "provision_worktree",
    "release_worktree",
]

DEFAULT_GIT_TIMEOUT_SECONDS = 60.0

#: One lock for every worktree store in the process; same rationale and same
#: boundary as the lease and quarantine stores.
_PERSIST_LOCK = threading.RLock()

_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DOT_RUN_RE = re.compile(r"\.{2,}")


class WorktreeStoreError(RuntimeError):
    """The worktree claim store could not be read or written (fail-closed, never silent)."""


class WorktreeStatus(StrEnum):
    CLAIMED = "claimed"
    REMOVED = "removed"
    FAILED = "failed"


class WorktreeClaim(BaseModel):
    """One exclusive claim on a worktree path by a ``(run, node)``."""

    claim_id: str
    run_id: str
    node_id: str
    #: Absolute path of the claimed worktree, confined under the engine root.
    path: str
    repo_root: str
    base_ref: str = "HEAD"
    status: WorktreeStatus = WorktreeStatus.CLAIMED
    #: HEAD the worktree was created at — what this task actually saw.
    head_commit: str = ""
    created_at: float = Field(default_factory=time.time)
    released_at: float | None = None
    #: Why a claim ended, or why a release failed. Never invented.
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class ProvisionOutcome:
    """What one provision attempt did."""

    ok: bool
    claim: WorktreeClaim | None = None
    head_commit: str = ""
    reason: str = ""


def confined_worktree_path(root: Path, run_id: str, node_id: str) -> Path:
    """The deterministic path a ``(run, node)`` task works in.

    Sanitized to ``[A-Za-z0-9._-]`` — separators, spaces and runs of dots are
    collapsed — so a node id carrying path syntax cannot escape the root, and
    deterministic so a re-dispatch of the same node finds its own claim rather
    than racing a fresh path.
    """
    safe_run = _DOT_RUN_RE.sub("_", _SAFE_COMPONENT_RE.sub("_", run_id))[:64] or "run"
    safe_node = _DOT_RUN_RE.sub("_", _SAFE_COMPONENT_RE.sub("_", node_id))[:64] or "node"
    return Path(root) / f"{safe_run}__{safe_node}"


class WorktreeStore:
    """Durable registry of worktree claims, atomically replaced on every mutation.

    Construct with ``store_dir`` to persist (the engine does); without it the
    store is process-local. An unreadable or corrupt file raises
    :class:`WorktreeStoreError` rather than answering as an empty registry —
    "nobody holds a worktree" and "I could not look" lead to opposite
    decisions, and the second one must never be rendered as the first.
    """

    def __init__(self, store_dir: Path | None = None, *, store_name: str = "worktrees.json") -> None:
        self._path = (Path(store_dir) / store_name) if store_dir is not None else None
        self._claims: dict[str, WorktreeClaim] = {}
        if self._path is not None:
            self._load()

    @property
    def store_path(self) -> Path | None:
        return self._path

    def _load(self) -> None:
        path = self._path
        if path is None:
            return
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            raise WorktreeStoreError(f"failed to read worktree store {path}: {exc}") from exc
        if not raw.strip():
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WorktreeStoreError(f"worktree store {path} is not valid JSON: {exc}") from exc
        version = payload.get("schema_version")
        if version != 1:
            raise WorktreeStoreError(f"worktree store {path} has unsupported schema_version {version!r} (expected 1)")
        for key, entry in (payload.get("claims") or {}).items():
            try:
                self._claims[key] = WorktreeClaim.model_validate(entry)
            except Exception as exc:  # noqa: BLE001 - a malformed record is a store error, not a crash
                raise WorktreeStoreError(f"worktree store {path} has a malformed claim for {key!r}: {exc}") from exc

    def _persist(self) -> None:
        path = self._path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        try:
            with _PERSIST_LOCK:
                payload = {
                    "schema_version": 1,
                    "claims": {key: claim.to_dict() for key, claim in self._claims.items()},
                }
                tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
                os.replace(tmp, path)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise WorktreeStoreError(f"failed to persist worktree store {path}: {exc}") from exc

    # -------------------------------------------------------------------- claims

    def active_holder(self, path: str) -> WorktreeClaim | None:
        """The ACTIVE claim on ``path``, if any. Exclusion is the whole point."""
        for claim in self._claims.values():
            if claim.status is WorktreeStatus.CLAIMED and claim.path == str(path):
                return claim
        return None

    def claim(self, *, run_id: str, node_id: str, path: Path, repo_root: str, base_ref: str = "HEAD") -> WorktreeClaim:
        """Record an exclusive claim, refusing a path another task already holds.

        The check and the write are one atomic unit under the persist lock: a
        wave dispatches nodes concurrently, and a task admitted between
        another task's check and write is exactly the double-claim this store
        exists to prevent.
        """
        key_path = str(path)
        with _PERSIST_LOCK:
            holder = self.active_holder(key_path)
            if holder is not None:
                raise WorktreeStoreError(f"worktree path {key_path} is already claimed by run '{holder.run_id}' node '{holder.node_id}' (claim {holder.claim_id}); one task per worktree")
            claim = WorktreeClaim(
                claim_id=f"wt_{uuid.uuid4().hex[:12]}",
                run_id=run_id,
                node_id=node_id,
                path=key_path,
                repo_root=str(repo_root),
                base_ref=base_ref,
            )
            self._claims[claim.claim_id] = claim
            self._persist()
            return claim

    def get(self, claim_id: str) -> WorktreeClaim | None:
        return self._claims.get(claim_id)

    def for_run_node(self, run_id: str, node_id: str) -> WorktreeClaim | None:
        for claim in self._claims.values():
            if claim.run_id == run_id and claim.node_id == node_id:
                return claim
        return None

    def list(self, *, run_id: str | None = None, status: WorktreeStatus | None = None, limit: int = 100) -> list[WorktreeClaim]:
        claims = list(self._claims.values())
        if run_id is not None:
            claims = [claim for claim in claims if claim.run_id == run_id]
        if status is not None:
            claims = [claim for claim in claims if claim.status is status]
        claims.sort(key=lambda claim: (claim.created_at, claim.claim_id))
        return claims[: max(0, limit)]

    def mark_provisioned(self, claim_id: str, *, head_commit: str) -> WorktreeClaim:
        with _PERSIST_LOCK:
            claim = self._require(claim_id)
            claim.head_commit = head_commit
            claim.status = WorktreeStatus.CLAIMED
            self._persist()
            return claim

    def mark_failed(self, claim_id: str, *, reason: str) -> WorktreeClaim:
        with _PERSIST_LOCK:
            claim = self._require(claim_id)
            claim.status = WorktreeStatus.FAILED
            claim.reason = reason
            self._persist()
            return claim

    def mark_released(self, claim_id: str, *, reason: str = "") -> WorktreeClaim:
        with _PERSIST_LOCK:
            claim = self._require(claim_id)
            claim.status = WorktreeStatus.REMOVED
            claim.released_at = time.time()
            claim.reason = reason
            self._persist()
            return claim

    def mark_release_failed(self, claim_id: str, *, reason: str) -> WorktreeClaim:
        """A failed removal leaves the claim ACTIVE with the real error.

        Marking it removed would report a worktree as gone while it still
        occupies the path — and the exclusion the store enforces would silently
        hand the same directory to the next task.
        """
        with _PERSIST_LOCK:
            claim = self._require(claim_id)
            claim.reason = reason
            self._persist()
            return claim

    def _require(self, claim_id: str) -> WorktreeClaim:
        claim = self._claims.get(claim_id)
        if claim is None:
            raise KeyError(f"worktree claim '{claim_id}' not found")
        return claim

    def forget_run(self, run_id: str) -> int:
        with _PERSIST_LOCK:
            doomed = [key for key, claim in self._claims.items() if claim.run_id == run_id]
            for key in doomed:
                del self._claims[key]
            if doomed:
                self._persist()
            return len(doomed)


# --------------------------------------------------------------------- git seams


def _run_git(args: list[str], *, timeout_seconds: float) -> tuple[bool, str, str]:
    """One argv-only, timeout-bounded git call. No shell, ever."""
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, no shell
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_seconds)),
            check=False,
        )
    except FileNotFoundError as exc:
        return False, "", f"git is not available on this host: {exc}"
    except subprocess.TimeoutExpired:
        return False, "", f"git {' '.join(args[:2])} timed out after {timeout_seconds:g}s"
    except OSError as exc:
        return False, "", f"git {' '.join(args[:2])} failed to start: {exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        return False, "", f"git {' '.join(args[:2])} exited {completed.returncode}: {detail[-1] if detail else 'no output'}"
    return True, completed.stdout.strip(), ""


def provision_worktree(
    *,
    path: Path,
    repo_root: str,
    base_ref: str,
    timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
) -> ProvisionOutcome:
    """Create a real detached worktree and return the head it was created at.

    Two git calls, both bounded: ``worktree add --detach`` then ``rev-parse
    HEAD`` inside the new worktree. The recorded head is what makes "what did
    this task actually see" answerable later; a creation whose head cannot be
    read is a failure, not a claim.
    """
    root = Path(repo_root)
    if not root.is_dir():
        return ProvisionOutcome(ok=False, reason=f"repo root {root} is not a directory on this host")
    ok, out, reason = _run_git(["-C", str(root), "rev-parse", "--git-dir"], timeout_seconds=timeout_seconds)
    if not ok:
        return ProvisionOutcome(ok=False, reason=f"{root} is not a git repository: {reason}")
    ok, _out, reason = _run_git(
        ["-C", str(root), "worktree", "add", "--detach", str(path), str(base_ref)],
        timeout_seconds=timeout_seconds,
    )
    if not ok:
        return ProvisionOutcome(ok=False, reason=f"worktree creation failed: {reason}")
    ok, head, reason = _run_git(["-C", str(path), "rev-parse", "HEAD"], timeout_seconds=timeout_seconds)
    if not ok:
        return ProvisionOutcome(ok=False, reason=f"worktree created but its head could not be read: {reason}")
    return ProvisionOutcome(ok=True, head_commit=head)


def release_worktree(
    *,
    path: Path,
    repo_root: str,
    timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Remove a provisioned worktree. ``(ok, reason)``; a failure is disclosed.

    ``--force`` is required because a task worktree routinely holds uncommitted
    or discarded edits — that is what an isolated checkout is for. The force is
    confined to the claimed path, which the store already proved is this
    task's own.
    """
    root = Path(repo_root)
    ok, _out, reason = _run_git(["-C", str(root), "worktree", "remove", "--force", str(path)], timeout_seconds=timeout_seconds)
    if not ok:
        return False, reason
    ok, _out, reason = _run_git(["-C", str(root), "worktree", "prune"], timeout_seconds=timeout_seconds)
    if not ok:
        # The worktree is gone; a stale prune is a maintenance detail, not a
        # failed release.
        return True, f"removed; prune reported: {reason}"
    return True, ""


def sanitized_repo_root(candidate: str) -> str:
    """Normalize a repo root for allowlist comparison (separators and case).

    Used by the allowlist check only — never to construct a path. Comparing
    declared roots needs one normalization so ``C:\\Repo`` and ``c:/repo`` are
    one allowlist entry on Windows, and that is all this does.
    """
    return str(PureWindowsPath(str(candidate).strip())).lower()
