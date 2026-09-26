"""The restart hook: one sweep that resumes every durable work unit.

Graph runs and model nodes already have a recovery path
(:mod:`app.gateway.run_recovery`, :mod:`alpha.orchestrator.replay`). Swarm tasks
and bot/subagent work did not: their durable state survived a crash but nothing
ever looked at it again, so the work silently stayed unfinished.

This module is that missing caller. :func:`install_restart_hooks` is idempotent
and is the single place a process should wire recovery at startup; the actual
sweep lives in :func:`alpha.runtime.escalation.recover_incomplete_work` so the
mechanism (ledger, attempt ceiling, escalation) stays in one module next to the
records it writes.

Honest scope: this is a SEAM, not a silent import side effect. Wiring it into
the Gateway lifespan needs one call at startup::

    from alpha.orchestrator.restart import install_restart_hooks
    install_restart_hooks()

Every registered hook is fail-soft and per-domain isolated, so one subsystem
that cannot be loaded is reported and the others still resume.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

RestartHook = Callable[[], dict[str, Any]]

_HOOKS: list[RestartHook] = []
# Re-entrant on purpose: ``install_restart_hooks`` holds this lock while it
# calls ``register_restart_hook``, which takes it again.
_HOOKS_LOCK = threading.RLock()
_INSTALLED = False


def register_restart_hook(hook: RestartHook) -> None:
    """Register an extra recovery sweep to run at startup."""
    with _HOOKS_LOCK:
        if hook not in _HOOKS:
            _HOOKS.append(hook)


def registered_hooks() -> tuple[RestartHook, ...]:
    with _HOOKS_LOCK:
        return tuple(_HOOKS)


def run_restart_recovery(*, now: float | None = None, limit: int = 50, start_swarm_runner: bool = True) -> dict[str, Any]:
    """Resume interrupted work across every domain that has durable state."""
    from alpha.runtime.escalation import recover_incomplete_work

    return recover_incomplete_work(now=now, limit=limit, start_swarm_runner=start_swarm_runner)


def run_restart_hooks(*, now: float | None = None) -> dict[str, Any]:
    """Run every registered restart hook, isolating failures per hook."""
    stamp = float(now if now is not None else time.time())
    report: dict[str, Any] = {"at": stamp, "hooks": []}
    for index, hook in enumerate(registered_hooks()):
        try:
            result = hook()
            report["hooks"].append({"index": index, "ok": True, "result": result})
        except Exception as exc:  # noqa: BLE001 - one broken hook must not block the rest
            logger.warning("Restart hook %d failed: %s: %s", index, type(exc).__name__, exc, exc_info=True)
            report["hooks"].append({"index": index, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return report


def install_restart_hooks(*, run_now: bool = False) -> dict[str, Any] | None:
    """Register the work-unit recovery sweep. Idempotent; safe to call twice.

    With ``run_now=True`` the sweep also runs immediately, which is what a
    process that starts up *as* the recovered worker wants.
    """
    global _INSTALLED
    with _HOOKS_LOCK:
        if not _INSTALLED:
            register_restart_hook(run_restart_recovery)
            _INSTALLED = True
    if not run_now:
        return None
    return run_restart_hooks()
