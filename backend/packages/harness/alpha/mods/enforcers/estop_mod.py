"""FleetEstopMod — universal fleet-wide emergency stop circuit breaker."""

from __future__ import annotations

import logging
import time

from alpha.mods.context import CapabilityContext
from alpha.mods.types import (
    AlphaEvent,
    EventResult,
    ModPriority,
    NextHandler,
)

logger = logging.getLogger(__name__)


class FleetEstopMod:
    """Universal fleet-wide circuit breaker across tools, agent spawns, and autonomy loops.

    When ESTOP is engaged, all tool admissions, agent spawns, and autonomous supervisor
    ticks are immediately refused with a fail-closed DENY verdict.
    """

    name = "fleet_estop"
    version = "1.0.0"
    priority = int(ModPriority.EMERGENCY)
    required_capabilities = {"estop:control", "estop:read"}
    subscribed_events = {
        "tool.*",
        "agent.*",
        "mission.*",
        "turn.*",
        "autonomy.*",
        "run.*",
        "task.*",
        "bot.*",
        "group.*",
    }

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        if ctx.estop.is_engaged():
            st = ctx.estop.status()
            reason_str = st.get("reason") or "Emergency stop active across fleet."
            logger.warning(
                "FleetEstopMod tripped for event '%s' (run_id=%s): %s",
                event.name,
                event.correlation.run_id,
                reason_str,
            )
            return EventResult.deny(
                event=event,
                reason=f"FLEET_ESTOP_ACTIVE: {reason_str}",
                metadata={
                    "halted_by": self.name,
                    "engaged_at": st.get("engaged_at"),
                    "estop_status": st,
                    "timestamp": time.time(),
                },
            )
        return await next_fn(event)
