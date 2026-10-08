"""Autonomous controller mods for the Alpha Mod Kernel (AMK).

- BotModeMod: Autonomous goal-driven execution loop controller, stall detector, and self-healer.
- TaskRouterMod: Autonomous capability matcher, model-tier selector, and task router.
- FailureSentinelMod: Failure fingerprinting, anti-loop detection, and auto-repair routing.
"""

from __future__ import annotations

from alpha.mods.controllers.bot_mode_mod import BotMission, BotModeMod
from alpha.mods.controllers.failure_sentinel_mod import FailureRecord, FailureSentinelMod
from alpha.mods.controllers.task_router_mod import TaskRouterMod

__all__ = [
    "BotMission",
    "BotModeMod",
    "FailureRecord",
    "FailureSentinelMod",
    "TaskRouterMod",
]
