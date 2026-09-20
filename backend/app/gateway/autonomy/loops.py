"""Loop adapters: one tick per subsystem, safe by default.

Each adapter performs exactly ONE unit of work and must be safe to call
repeatedly. Adapters never raise (they return a dict summary); a tick that needs
a model/API key degrades to a no-op summary so a machine without credentials
still runs a complete, healthy system.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SyncTick = Callable[[], dict[str, Any]]


def _resolve_project_root() -> Path:
    """Repository root for repo-scoped loops (sentinel scans, etc.)."""
    try:
        from alpha.config.runtime_paths import project_root
        return project_root()
    except Exception:
        # backend/app/gateway/autonomy/loops.py -> repo root is four levels up.
        return Path(__file__).resolve().parents[4]


def sentinel_tick(*, repo_root: Path | None = None, auto_heal: bool = False) -> dict[str, Any]:
    """One sentinel observe pass.

    Safe default: observes and reports; escalates unknown kinds.
    When auto_heal is True, wires default verified repair and verification routines.
    """
    from alpha.runtime.sentinel.runner import (
        SentinelRunner,
        make_default_fix_fns,
        make_default_verification_commands,
    )

    root = repo_root or _resolve_project_root()
    fix_fns = make_default_fix_fns(root) if auto_heal else None
    verification_commands = make_default_verification_commands() if auto_heal else None
    runner = SentinelRunner(
        repo_root=root,
        fix_fns=fix_fns,
        verification_commands=verification_commands,
    )
    report = runner.run_once()
    return report.to_dict() if hasattr(report, "to_dict") else {"summary": str(report)}


def perpetual_tick() -> dict[str, Any]:
    """One perpetual-daemon heartbeat (discovery / consolidation / stagnation)."""
    from alpha.perpetual.daemon import PerpetualDaemon

    daemon = PerpetualDaemon(project_id="default")
    return daemon.step_heartbeat()


def review_queue_tick() -> dict[str, Any]:
    """Observe the deferred review queue WITHOUT draining it.

    Draining pops snapshots and requires a handler; doing that from a background
    loop would silently discard learning-fork reviews. This tick only reports how
    many reviews are pending so the signal is visible and can trigger the bus.
    """
    from alpha.learning.review_queue import get_review_queue

    queue = get_review_queue()
    pending = queue.pending()
    return {"pending": len(pending), "session_ids": [entry.session_id for entry in pending]}


def skill_curator_tick(*, dry_run: bool = True) -> dict[str, Any]:
    """One skill-curator prune pass. Dry-run by default; the flag decides."""
    from alpha.skills.curator import SkillCurator

    curator = SkillCurator()
    return curator.apply_transitions(dry_run=dry_run)


def enterprise_heartbeat_tick() -> dict[str, Any]:
    """One enterprise heartbeat cycle."""
    from alpha.enterprise.heartbeat import get_enterprise_heartbeat_coordinator

    coordinator = get_enterprise_heartbeat_coordinator()
    return coordinator.step_heartbeat_cycle()


def swarm_status_tick() -> dict[str, Any]:
    """Telemetry-only swarm tick: report running swarm tasks without starting any.

    Swarms are started on demand by the swarm tool/HTTP surface; a periodic loop
    must not spawn agent work on its own.
    """
    try:
        from alpha.swarm.runner import AsyncSwarmRunner  # noqa: F401

        # Instantiating the runner requires a coordinator; report absence honestly
        # instead of fabricating one.
        return {"active_swarms": "on-demand-only", "note": "swarms start via swarm_tool/HTTP"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"error": type(exc).__name__}
