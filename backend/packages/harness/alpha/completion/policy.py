"""Policy resolution: ``config.yaml -> completion`` into one immutable snapshot.

Every gate run, path claim and finalization decision reads the *same*
:class:`CompletionSettings` value, and that snapshot is what a definition
digest binds (``evidence.definition_digest``). Changing a policy value
therefore invalidates prior approvals and makes prior evidence stale instead
of quietly re-interpreting it — which is the property the plan asks for under
"approval invalidation" and "evidence bound to policy id".

``settings_from_config`` is deliberately a pure function of an explicit
``CompletionConfig``: the engine takes a snapshot off the event loop (the
Gateway reads config through ``asyncio.to_thread``) and never caches
``AppConfig`` itself, because ``get_app_config()`` stats the file on every
call and the hot-reload contract belongs to it, not to us.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from alpha.config.completion_config import CompletionConfig
from alpha.config.runtime_paths import project_root, runtime_home

from .errors import PathEscape


@dataclass(frozen=True)
class CompletionSettings:
    """An immutable policy snapshot for one engine call."""

    enabled: bool
    mode: str
    workspace_root: Path
    store_path: Path
    gate_timeout_seconds: float
    max_output_bytes: int
    max_tasks: int
    max_depth: int
    max_concurrency: int
    max_gate_runs: int
    wall_clock_seconds: float
    no_progress_limit: int
    allow_network_checks: bool
    preapproved_commands: frozenset[str] = field(default_factory=frozenset)
    ownership_lease_seconds: float = 1800.0
    max_scopes: int = 200
    max_events: int = 2000

    @property
    def enforcing(self) -> bool:
        """True only in ``enforce`` mode — observe mode never mutates status."""
        return self.mode == "enforce"

    @property
    def guarding(self) -> bool:
        """Whether this snapshot may mutate status: enabled *and* enforce mode.

        ``enforcing`` is the mode alone, so a disabled engine left on the
        default mode would otherwise report — and act as — a live gate.
        """
        return self.enabled and self.enforcing

    @property
    def policy_id(self) -> str:
        """Short digest of the *policy* fields evidence and approvals bind to.

        Not every field participates: ``max_scopes``/``max_events`` are
        retention knobs and cannot change what a check proved, so including
        them would invalidate evidence for an irrelevant edit.
        """
        payload = {
            "mode": self.mode,
            "workspace": str(self.workspace_root),
            "timeout": self.gate_timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "allow_network": self.allow_network_checks,
            "preapproved": sorted(self.preapproved_commands),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    def resolve(self, relative: str) -> Path:
        """Resolve a declared path inside the workspace, refusing any escape.

        Resolution normalises first and then refuses whatever landed outside —
        the same shape ``alpha.avo.paths`` documents. The refusal is not
        repaired into a path that happens to fit: a declared ``..`` that
        leaves the workspace raises :class:`PathEscape` carrying the original
        text, so a gate that reads outside the boundary says so instead of
        quietly pointing somewhere else.
        """
        candidate = Path(relative)
        root = self.workspace_root
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise PathEscape(f"path {relative!r} resolves outside the workspace root {str(root)!r}", subject=relative) from exc
        return resolved

    def relpath(self, path: Path) -> str:
        """Workspace-relative POSIX form of a resolved path (for fingerprints)."""
        try:
            return path.resolve().relative_to(self.workspace_root).as_posix()
        except ValueError:
            return path.resolve().as_posix()


def settings_from_config(config: CompletionConfig | None = None) -> CompletionSettings:
    """Build the snapshot. With no argument it reads the live app config."""
    if config is None:
        from alpha.config import get_app_config

        config = get_app_config().completion
    workspace = Path(config.workspace_root).expanduser() if config.workspace_root else project_root()
    workspace = workspace.resolve()
    store = Path(config.store_path).expanduser() if config.store_path else (runtime_home() / "completion_store" / "state.json")
    return CompletionSettings(
        enabled=bool(config.enabled),
        mode=str(config.mode),
        workspace_root=workspace,
        store_path=store.resolve(),
        gate_timeout_seconds=float(config.gate_timeout_seconds),
        max_output_bytes=int(config.max_output_bytes),
        max_tasks=int(config.max_tasks),
        max_depth=int(config.max_depth),
        max_concurrency=int(config.max_concurrency),
        max_gate_runs=int(config.max_gate_runs),
        wall_clock_seconds=float(config.wall_clock_seconds),
        no_progress_limit=int(config.no_progress_limit),
        allow_network_checks=bool(config.allow_network_checks),
        preapproved_commands=frozenset(name.strip() for name in config.preapproved_commands if name.strip()),
        ownership_lease_seconds=float(config.ownership_lease_seconds),
        max_scopes=int(config.max_scopes),
        max_events=int(config.max_events),
    )


__all__ = ["CompletionSettings", "settings_from_config"]
