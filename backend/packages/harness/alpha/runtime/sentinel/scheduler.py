"""Executes registered bot routines — the trigger behind `sentinel-observe`.

The Sentinel bot registers a routine (`sentinel-observe`, `*/5 * * * *` ->
`sentinel.run_once`), but nothing executed it. This module closes that gap.

It is deliberately conservative:
- Routines are opt-in via `enabled` (default true) but the *action* must be in
  ACTIONS. An unknown action is skipped and reported, never guessed at.
- Every invocation is wrapped so one failing routine cannot kill the scheduler.
- `run_forever` is bounded by `max_iterations` and uses an injectable sleep so it
  is testable without waiting.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: Supported routine actions. Anything else is skipped (never guessed).
ACTIONS: tuple[str, ...] = ("sentinel.run_once",)

#: Matches the "*/N * * * *" form used by the Sentinel routine. Anything else is
#: reported as unsupported rather than silently mis-scheduled.
_EVERY_N = re.compile(r"^\*/(\d+)\s+\*\s+\*\s+\*\s+\*$")


@dataclass
class RoutineRun:
    bot: str
    name: str
    action: str
    status: str                 # "ok" | "skipped" | "failed"
    detail: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bot": self.bot, "name": self.name, "action": self.action,
            "status": self.status, "detail": self.detail, "result": self.result,
        }


def interval_seconds(schedule: str) -> float | None:
    """Parse '*/N * * * *' into seconds. None when unsupported."""
    m = _EVERY_N.match((schedule or "").strip())
    if not m:
        return None
    n = int(m.group(1))
    return float(n * 60) if n > 0 else None


class RoutineScheduler:
    """Runs enabled bot routines whose action is supported."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        registry_factory: Callable[[], Any] | None = None,
        runner_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self._registry_factory = registry_factory
        self._runner_factory = runner_factory

    def _registry(self) -> Any:
        if self._registry_factory is not None:
            return self._registry_factory()
        from alpha.bots.registry import BotRegistry

        return BotRegistry()

    def _runner(self) -> Any:
        if self._runner_factory is not None:
            return self._runner_factory()
        from alpha.runtime.sentinel.runner import (
            SentinelRunner,
            make_default_fix_fns,
        )

        return SentinelRunner(self.repo_root, fix_fns=make_default_fix_fns(self.repo_root))

    # -- one pass over all bots -------------------------------------------
    def run_once(self) -> list[RoutineRun]:
        runs: list[RoutineRun] = []
        try:
            registry = self._registry()
            bots = list(registry.list_bots(include_archived=False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("routine scan failed: %s: %s", type(exc).__name__, exc)
            return runs

        for bot in bots:
            for routine in getattr(bot, "routines", []) or []:
                if not routine.get("enabled", True):
                    continue
                runs.append(self._run_routine(bot, routine))
        return runs

    def _run_routine(self, bot: Any, routine: dict[str, Any]) -> RoutineRun:
        name = str(routine.get("name") or "")
        action = str(routine.get("action") or "")
        bot_name = getattr(bot, "name", "?")

        if action not in ACTIONS:
            return RoutineRun(bot_name, name, action, "skipped",
                              f"unsupported action {action!r}")

        try:
            if action == "sentinel.run_once":
                report = self._runner().run_once()
                return RoutineRun(bot_name, name, action, "ok",
                                  report.summary(), report.to_dict())
        except Exception as exc:  # noqa: BLE001 - one routine must not kill the pass
            logger.warning("routine %s failed", name, exc_info=True)
            return RoutineRun(bot_name, name, action, "failed",
                              f"{type(exc).__name__}: {exc}")

        return RoutineRun(bot_name, name, action, "skipped", "no handler")

    # -- daemon ------------------------------------------------------------
    def run_forever(
        self,
        *,
        interval_seconds_: float = 300.0,
        max_iterations: int | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> list[list[RoutineRun]]:
        """Poll on an interval. Bounded by max_iterations (None = forever)."""
        out: list[list[RoutineRun]] = []
        i = 0
        while max_iterations is None or i < max_iterations:
            out.append(self.run_once())
            i += 1
            if max_iterations is None or i < max_iterations:
                sleep_fn(interval_seconds_)
        return out
