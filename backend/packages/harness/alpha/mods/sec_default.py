"""SecDefaultMod — the first-loaded guard over the Alpha Mod Kernel.

Claude Code loads a built-in ``sec-default`` mod **first** on every machine with
managed settings, precisely so nothing a user installs afterwards can outrank
it. Its job is narrow and specific: stop user-installed mods from weakening what
the organization manages — overriding a permission ``deny`` rule, for example.

Alpha's equivalent guards the *control chain*, because that is where the same
weakness lives here. It has two halves, and neither is optional:

**1. Registration guards (the onion).** A mod registered at or below
``SECURITY`` priority runs before the security and emergency triad, so it could
neutralize them. An external mod may never take that seat without an explicit
operator grant. Two narrower rules follow from the same property:

- an external mod may never be granted a capability it did not *declare*
  (a grant is a review, so granting more than was reviewed is granting
  unreviewed power), and
- no external mod may hold ``estop:control`` — engaging or clearing a
  fleet-wide emergency stop is a first-party safety authority.

**2. The runtime gating invariant.** The docs are blunt that a mod which can
approve tool calls can approve one an operator's deny rule refused. So this mod
wraps the whole chain and, on a *gating* event, refuses to let a non-first-party
mod's ``ANSWER`` stand. A third-party mod may observe, rewrite within its own
event, and deny; it may not decide the outcome of a security decision.

Only first-party ``alpha.mods.*`` code is exempt, and that exemption is a
module-path check, not a security boundary: dropping a file into
``alpha/mods/`` makes it first-party *by doing so*. That is the same trust rule
the kernel has always applied to capability grants, stated in one place.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from alpha.mods.context import CapabilityContext
from alpha.mods.manifest import GUARDED_PRIORITY_CEILING, RESERVED_CAPABILITIES, ModManifest
from alpha.mods.types import (
    AlphaEvent,
    EventOutcome,
    EventResult,
    ModPriority,
    NextHandler,
)

if TYPE_CHECKING:
    from alpha.mods.kernel import ModKernel

logger = logging.getLogger(__name__)

#: Event families where a third-party mod answering is a policy decision, not a
#: feature. ``tool.*`` is the one that matters most: an ANSWER there means the
#: tool result was fabricated without the tool running.
GATING_EVENT_PREFIXES: tuple[str, ...] = (
    "tool.requested",
    "tool.completed",
    "run.admit",
    "permission.",
    "security.",
)

#: How many refusals this guard retains for operator inspection.
MAX_REFUSALS = 200


class SecDefaultMod:
    """First-loaded registration guard and runtime gating invariant."""

    name = "sec_default"
    version = "1.0.0"
    # The lowest tier that still sorts ahead of the emergency and security
    # triad. Sorting is stable and ascending, so equal priorities keep
    # registration order — installing the guard first is what makes it outermost.
    priority = int(ModPriority.KERNEL)
    required_capabilities: set[str] = set()
    subscribed_events = None
    manifest = ModManifest.create(
        name="sec_default",
        version="1.0.0",
        description="First-loaded registration guard and runtime gating invariant; nothing may outrank it.",
        hooks=("*",),
        calls=(),
        gating=True,
    )

    def __init__(self) -> None:
        self._kernel: ModKernel | None = None
        self._refusals: list[dict[str, Any]] = []
        self._installed_at: float | None = None
        self._lock = threading.RLock()

    # -- installation ------------------------------------------------------

    def attach(self, kernel: ModKernel) -> None:
        """Bind this guard to the kernel it protects.

        The kernel reference is set here rather than injected because the guard
        is constructed *before* the kernel it will be registered on exists in
        ``_register_builtin_enforcers``. The reference is only used to read the
        first-party decision and the chain of record; the guard never mutates
        the registry from inside a dispatch.
        """
        self._kernel = kernel
        self._installed_at = time.time()

    # -- registration guard ------------------------------------------------

    def guard_registration(self, mod: Any, granted_capabilities: set[str]) -> str | None:
        """Admit or refuse a registration. Returns ``None`` to admit."""
        name = str(getattr(mod, "name", mod.__class__.__name__))
        priority = int(getattr(mod, "priority", 2000))
        module = getattr(mod.__class__, "__module__", "") or ""
        first_party = module.startswith("alpha.mods.")
        granted = set(granted_capabilities or set())

        if first_party:
            return None

        if priority <= GUARDED_PRIORITY_CEILING:
            return self._refuse(
                "PRIORITY_CEILING",
                f"external mod registers at priority {priority}, at or below the guarded ceiling {GUARDED_PRIORITY_CEILING}; it would run before the security and emergency triad. Only first-party alpha.mods.* code may take that seat.",
                name=name,
                priority=priority,
            )

        declared = {str(c) for c in (getattr(mod, "required_capabilities", set()) or set())}
        undeclared = sorted(granted - declared)
        if undeclared:
            return self._refuse(
                "UNDECLARED_GRANT",
                f"external mod was granted {', '.join(undeclared)} which it does not declare in required_capabilities; a capability a mod never asked for is a capability nobody reviewed.",
                name=name,
                granted=sorted(granted),
                declared=sorted(declared),
            )

        reserved = sorted(granted & set(RESERVED_CAPABILITIES))
        if reserved:
            return self._refuse(
                "RESERVED_CAPABILITY",
                f"external mod was granted reserved capability {', '.join(reserved)}; tripping or clearing the fleet emergency stop is a first-party safety authority.",
                name=name,
                granted=sorted(granted),
            )

        return None

    def _refuse(self, code: str, reason: str, **context: Any) -> str:
        with self._lock:
            self._refusals.append(
                {
                    "code": code,
                    "reason": reason,
                    "timestamp": time.time(),
                    **context,
                }
            )
            overflow = len(self._refusals) - MAX_REFUSALS
            if overflow > 0:
                del self._refusals[:overflow]
        logger.warning("SecDefaultMod refused a registration: %s (%s)", code, reason)
        return f"[{code}] {reason}"

    def refusals(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._refusals[-max(0, int(limit)) :]]

    def status(self) -> dict[str, Any]:
        """The guard's own posture, for the Gateway's status projection."""
        chain = self._kernel.control_chain() if self._kernel is not None else []
        with self._lock:
            return {
                "installed": self._installed_at is not None,
                "installed_at": self._installed_at,
                "control_chain_length": len(chain),
                "first_mod": chain[0]["mod"] if chain else None,
                "guarded_priority_ceiling": GUARDED_PRIORITY_CEILING,
                "reserved_capabilities": sorted(RESERVED_CAPABILITIES),
                "gating_events": list(GATING_EVENT_PREFIXES),
                "refusal_count": len(self._refusals),
                "refusals": self.refusals(10),
            }

    # -- runtime -----------------------------------------------------------

    def _is_gating_event(self, event_name: str) -> bool:
        """True when answering this event is a policy decision, not a feature."""
        return any(event_name == prefix or event_name.startswith(f"{prefix}.") for prefix in GATING_EVENT_PREFIXES)

    def _owner_is_external(self, owner: str) -> bool:
        if not owner or self._kernel is None:
            return False
        mod = self._kernel.get_mod(owner)
        if mod is None:
            # A mod that answered and then unregistered itself is not a caller
            # this guard can vouch for; treating it as external fails closed.
            return True
        module = getattr(mod.__class__, "__module__", "") or ""
        return not module.startswith("alpha.mods.")

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        """Wrap the chain and refuse a gating ANSWER from external code."""
        result = await next_fn(event)

        try:
            if result.outcome == EventOutcome.ANSWER and self._is_gating_event(event.name) and self._kernel is not None:
                owner = self._kernel.find_outcome_owner(EventOutcome.ANSWER)
                if self._owner_is_external(owner):
                    self._refuse(
                        "GATING_ANSWER_REFUSED",
                        f"external mod '{owner}' answered gating event '{event.name}'; a third-party mod may observe, rewrite or deny, but it may not decide the outcome of a security decision.",
                        name=owner,
                        event=event.name,
                    )
                    return EventResult.deny(
                        event,
                        reason=(f"SEC_DEFAULT_GATING: external mod '{owner}' answered '{event.name}'. The security-default guard refused it; the action was not executed."),
                        metadata={
                            "halted_by": self.name,
                            "external_answerer": owner,
                            "gating_event": event.name,
                        },
                    )
        except Exception as exc:  # pragma: no cover - the guard must not crash the pipeline
            logger.error("SecDefaultMod failed while auditing outcome for '%s': %s", event.name, exc, exc_info=True)

        return result
