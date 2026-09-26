"""Runtime identity for this Alpha instance (architecture spec sections 4/5).

Answers "who am I, what repository contains my source, what version am I
running" from local facts only: the project manifest, package metadata, a
best-effort git probe, and the persisted update state. Nothing is fabricated —
unavailable facts come back as ``"unknown"`` plus an honest source/note, and
the only failure mode is an honest ``RuntimeError`` (missing manifest,
unwritable runtime home), never silent defaults.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import sys
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.manifest import find_project_manifest_path, load_project_manifest_at

logger = logging.getLogger(__name__)

IDENTITY_FILE = "identity.json"

# Version chain duplicated from app.gateway.routers.ops._resolve_gateway_version
# (the harness package must not import the gateway app). The drift test in
# tests/test_evolution_identity.py asserts
# resolve_alpha_version() == ops._resolve_gateway_version() so the two copies
# can never disagree silently.
_VERSION_DIST_NAMES = ("agent-workspace-harness", "alpha", "agent-workspace")

# Honest, complete list of capabilities actually wired in this build:
# GET /api/evolution/identity (this module), POST /api/evolution/update-check
# (release_check), the persistent evolution ledger (engine +
# GET /api/evolution/ledger), and the guarded Phase-2 source updater
# (admin apply/recover routes plus the `self_update` autonomy loop). Never
# list a capability without a code path.
WIRED_CAPABILITIES = ("identity", "release_check", "evolution_ledger", "auto_update")


def resolve_alpha_version() -> str:
    """Return the installed harness package version, or "unknown".

    Same 3-name chain as ops.py — see the comment on _VERSION_DIST_NAMES.
    """
    for name in _VERSION_DIST_NAMES:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return "unknown"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Persist payload as JSON via a unique tmp file + ``os.replace``.

    ``os.replace`` is atomic on Windows and POSIX, and the tmp name carries a
    uuid fragment so concurrent writers cannot corrupt each other's staging
    file. Callers decide whether a failure raises (identity) or degrades to a
    logged warning (update state).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        with suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def _load_or_create_identity_record() -> dict[str, Any]:
    """Load identity.json, generating and atomically persisting it once.

    An existing ``agentId`` is never overwritten (plain read-modify-write is
    fine for the single Gateway process). A file with no usable id cannot be
    recovered from, so a fresh id replaces it with a logged warning.
    """
    path = runtime_home() / IDENTITY_FILE
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and isinstance(existing.get("agentId"), str) and existing["agentId"].strip():
            return existing
        logger.warning("%s carries no usable agentId; generating a fresh identity", path)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s (%s); generating a fresh identity", path, exc)

    record = {
        "agentId": f"alpha-{uuid.uuid4().hex[:8]}",
        "identityVersion": 1,
        "createdAt": datetime.now(UTC).isoformat(),
    }
    try:
        atomic_write_json(path, record)
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"Could not persist the Alpha identity to {path}: {exc}") from exc
    return record


def _resolve_git_commit(repo_dir: Path) -> tuple[str, str, str]:
    """Best-effort ``(commit, source, note)`` via ``git -C <repo_dir>``.

    ``repo_dir`` is the manifest's directory; git discovers the repository
    upward from it. Never fabricates: every failure returns ``"unknown"``,
    source ``"unavailable"``, and the real reason as the note (timeout is 5s
    so a hung git can never stall identity assembly).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown", "unavailable", f"git rev-parse could not run: {exc}"
    commit = proc.stdout.strip()
    if proc.returncode != 0 or not commit:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit code {proc.returncode}"
        return "unknown", "unavailable", f"git rev-parse failed: {detail}"
    return commit, "git", ""


def get_runtime_identity() -> dict[str, Any]:
    """Assemble the Alpha Runtime Identity (spec section 4).

    Blocking I/O (disk + subprocess): Gateway callers run this through
    ``asyncio.to_thread``. ``updateState`` is the state-machine value from the
    persisted update state; full detail lives behind
    ``GET /api/evolution/update-state``.
    """
    from alpha.evolution.release_check import load_update_state  # local import: release_check imports this module's version helper (cycle otherwise)

    manifest_path = find_project_manifest_path()
    manifest = load_project_manifest_at(manifest_path)
    record = _load_or_create_identity_record()
    git_commit, git_commit_source, git_commit_note = _resolve_git_commit(manifest_path.parent)
    update_state = load_update_state()
    return {
        "agentId": record["agentId"],
        "identityVersion": record.get("identityVersion"),
        "createdAt": record.get("createdAt"),
        "alphaVersion": resolve_alpha_version(),
        "gitCommit": git_commit,
        "gitCommitSource": git_commit_source,
        "gitCommitNote": git_commit_note,
        "os": platform.system() or sys.platform,
        "architecture": platform.machine() or "unknown",
        "runtime": f"python {platform.python_version()}",
        "repository": dict(manifest["repository"]),
        "releaseChannel": manifest["release"]["channel"],
        "updateState": update_state.get("state", "unknown"),
        "capabilities": list(WIRED_CAPABILITIES),
    }
