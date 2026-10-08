"""The Alpha Mod Kernel (AMK) — deterministic ordered middleware execution engine."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from collections.abc import Callable
from threading import RLock
from typing import Any

from alpha.mods.context import CapabilityContext
from alpha.mods.types import (
    AlphaEvent,
    AlphaMod,
    EventOutcome,
    EventResult,
    ModPriority,
    NextHandler,
)

logger = logging.getLogger(__name__)


class ModAdmissionError(RuntimeError):
    """A policy gate could not affirmatively admit a protected operation."""


class ModKernel:
    """The central execution engine orchestrating registered mods across priority tiers.

    Execution proceeds in strict ascending order of priority:
    KERNEL (0) -> EMERGENCY (100) -> SECURITY (200) -> BUDGET (300) ->
    AUTONOMY (500) -> EXECUTION (700) -> VERIFICATION (800) ->
    RECOVERY (900) -> OBSERVABILITY (1000) -> USER_EXTENSIONS (2000).
    """

    def __init__(self, *, max_journal_size: int = 500):
        self._mods: list[AlphaMod] = []
        self._capabilities: dict[str, set[str]] = {}
        self._max_journal_size = max_journal_size
        self._journal: list[dict[str, Any]] = []
        self._tool_executors: dict[str, Callable[..., Any]] = {}
        self._mock_model_provider: Callable[..., Any] | None = None
        self._estop_release_secret: str | None = None
        self._evidence_ledger: dict[str, dict[str, Any]] = {}
        self._evidence_lock = RLock()
        self._lock = asyncio.Lock()

    def register_mod(
        self,
        mod: AlphaMod,
        granted_capabilities: set[str] | None = None,
    ) -> None:
        """Register a mod and keep the pipeline strictly sorted by priority."""
        # Unregister existing mod with the same name if present
        self.unregister_mod(mod.name)

        # A mod's declaration is a request, never a grant. First-party Alpha
        # modules receive their reviewed declarations by default; external mods
        # need an explicit operator grant at registration.
        if granted_capabilities is None:
            module_name = getattr(mod.__class__, "__module__", "")
            caps = set(getattr(mod, "required_capabilities", set())) if module_name.startswith("alpha.mods.") else set()
        else:
            caps = set(granted_capabilities)

        self._mods.append(mod)
        self._capabilities[mod.name] = caps
        # Deterministic stable sort by priority integer ascending
        self._mods.sort(key=lambda m: getattr(m, "priority", ModPriority.USER_EXTENSIONS))
        logger.info(
            "Registered mod '%s' (v%s, priority=%d, capabilities=%s)",
            mod.name,
            getattr(mod, "version", "1.0.0"),
            mod.priority,
            sorted(caps),
        )

    def unregister_mod(self, name: str) -> bool:
        """Remove a registered mod by name."""
        initial_len = len(self._mods)
        self._mods = [m for m in self._mods if m.name != name]
        self._capabilities.pop(name, None)
        return len(self._mods) < initial_len

    def get_mod(self, name: str) -> AlphaMod | None:
        """Find a registered mod by name."""
        for m in self._mods:
            if m.name == name:
                return m
        return None

    def list_mods(self) -> list[AlphaMod]:
        """Return list of all registered mods sorted by priority."""
        return list(self._mods)

    def get_granted_capabilities(self, mod_name: str) -> set[str]:
        return set(self._capabilities.get(mod_name, set()))

    def _matches_event(self, mod: AlphaMod, event_name: str) -> bool:
        """Determine whether a mod subscribes to the given event name."""
        subs = getattr(mod, "subscribed_events", None)
        if subs is None:
            return True
        if isinstance(subs, str):
            subs = [subs]
        for pattern in subs:
            if pattern == event_name or fnmatch.fnmatch(event_name, pattern):
                return True
        return False

    def _create_context(self, mod: AlphaMod) -> CapabilityContext:
        caps = self._capabilities.get(mod.name, set())
        return CapabilityContext(mod.name, caps, self)

    async def dispatch(
        self,
        event: AlphaEvent,
        terminal_handler: NextHandler | None = None,
    ) -> EventResult:
        """Executes the ordered middleware pipeline for the given event."""
        start_time = time.time()
        matching_mods = [m for m in self._mods if self._matches_event(m, event.name)]

        async def _default_terminal(ev: AlphaEvent) -> EventResult:
            return EventResult.continue_(ev)

        terminal = terminal_handler or _default_terminal

        async def _compose(index: int, current_event: AlphaEvent) -> EventResult:
            if index >= len(matching_mods):
                return await terminal(current_event)

            mod = matching_mods[index]
            ctx = self._create_context(mod)

            next_called = False

            async def _next(next_ev: AlphaEvent) -> EventResult:
                nonlocal next_called
                next_called = True
                return await _compose(index + 1, next_ev)

            try:
                res = await mod.handle(ctx, current_event, _next)
                if not isinstance(res, EventResult):
                    if mod.priority <= ModPriority.SECURITY:
                        logger.critical(
                            "Critical mod '%s' returned invalid type %s (FAIL CLOSED)",
                            mod.name,
                            type(res),
                        )
                        return EventResult.deny(
                            current_event,
                            reason=f"CRITICAL_MOD_FAULT: {mod.name} returned invalid result {type(res).__name__}",
                        )
                    logger.warning(
                        "Mod '%s' returned invalid type %s; coercing to CONTINUE",
                        mod.name,
                        type(res),
                    )
                    return await _compose(index + 1, current_event)

                if res.outcome in (EventOutcome.CONTINUE, EventOutcome.OBSERVE) and not next_called:
                    return await _compose(index + 1, res.event or current_event)

                if res.outcome == EventOutcome.REWRITE:
                    rewritten = res.event
                    if rewritten.name != current_event.name or rewritten.event_id != current_event.event_id or rewritten.correlation != current_event.correlation or rewritten.source != current_event.source:
                        return EventResult.deny(
                            current_event,
                            reason=f"INVALID_REWRITE: mod '{mod.name}' changed event identity",
                        )
                    downstream = await _compose(index + 1, rewritten)
                    rewrite = {"mod": mod.name, "reason": res.reason, **res.metadata}
                    metadata = dict(downstream.metadata)
                    rewrites = list(metadata.get("mod_rewrites", []))
                    rewrites.append(rewrite)
                    metadata["mod_rewrites"] = rewrites
                    return EventResult(
                        outcome=downstream.outcome,
                        event=downstream.event,
                        response_payload=downstream.response_payload,
                        reason=downstream.reason,
                        error=downstream.error,
                        metadata=metadata,
                    )
                return res
            except Exception as exc:
                # Security and Emergency mods fail closed!
                if mod.priority <= ModPriority.SECURITY:
                    logger.critical(
                        "Critical security/emergency mod '%s' raised exception: %s (FAIL CLOSED)",
                        mod.name,
                        exc,
                        exc_info=True,
                    )
                    return EventResult.deny(
                        current_event,
                        reason=f"CRITICAL_MOD_FAULT: {mod.name}: {exc}",
                        error=exc,
                    )
                # Non-critical mods (observability, etc.) fail open with warning
                logger.warning(
                    "Non-critical mod '%s' raised exception: %s (FAIL OPEN, continuing)",
                    mod.name,
                    exc,
                    exc_info=True,
                )
                self._record_journal(
                    event=current_event,
                    outcome=EventOutcome.CONTINUE,
                    reason=f"FAULT_ISOLATED: {mod.name} failed with {exc}",
                    duration=time.time() - start_time,
                    fault_mod=mod.name,
                )
                return await _compose(index + 1, current_event)

        final_result = await _compose(0, event)
        duration = time.time() - start_time
        self._record_journal(
            event=event,
            outcome=final_result.outcome,
            reason=final_result.reason,
            duration=duration,
        )
        return final_result

    def _record_journal(
        self,
        *,
        event: AlphaEvent,
        outcome: EventOutcome,
        reason: str,
        duration: float,
        fault_mod: str | None = None,
    ) -> None:
        entry = {
            "timestamp": time.time(),
            "event_name": event.name,
            "event_id": event.event_id,
            "correlation": event.correlation.to_dict(),
            "outcome": outcome.value if hasattr(outcome, "value") else str(outcome),
            "reason": reason,
            "duration": duration,
        }
        if fault_mod:
            entry["fault_mod"] = fault_mod
        self._journal.append(entry)
        if len(self._journal) > self._max_journal_size:
            self._journal.pop(0)

    def get_journal(self, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieve recent dispatched events and decisions."""
        return list(self._journal[-limit:])

    def clear_journal(self) -> None:
        self._journal.clear()

    # -- Capability hooks --
    def register_tool_executor(self, name: str, fn: Callable[..., Any]) -> None:
        self._tool_executors[name] = fn

    def get_tool_executor(self, name: str) -> Callable[..., Any] | None:
        return self._tool_executors.get(name)

    def list_registered_tools(self) -> list[str]:
        return list(self._tool_executors.keys())

    def set_mock_model_provider(self, fn: Callable[..., Any] | None) -> None:
        self._mock_model_provider = fn

    def get_mock_model_provider(self) -> Callable[..., Any] | None:
        return self._mock_model_provider

    def set_estop_release_secret(self, secret: str | None) -> None:
        self._estop_release_secret = secret

    def get_estop_release_secret(self) -> str | None:
        return self._estop_release_secret


# -- Process-wide singleton --
_global_kernel: ModKernel | None = None


def get_mod_kernel() -> ModKernel:
    """Retrieve the process-wide ModKernel singleton."""
    global _global_kernel
    if _global_kernel is None:
        kernel = ModKernel()
        _register_builtin_enforcers(kernel)
        _global_kernel = kernel
    return _global_kernel


def set_mod_kernel(kernel: ModKernel | None) -> None:
    """Explicitly assign the process-wide ModKernel singleton."""
    global _global_kernel
    _global_kernel = kernel


def reset_mod_kernel() -> ModKernel:
    """Reset and re-initialize the process-wide ModKernel singleton."""
    global _global_kernel
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)
    _global_kernel = kernel
    return kernel


def _register_builtin_enforcers(kernel: ModKernel) -> None:
    """Register first-party safety enforcers and autonomous controllers by default."""
    from alpha.mods.enforcers.blast_radius_mod import BlastRadiusGuardMod
    from alpha.mods.enforcers.estop_mod import FleetEstopMod
    from alpha.mods.enforcers.verification_gate_mod import VerificationEvidenceGateMod

    for mod in (FleetEstopMod(), BlastRadiusGuardMod(), VerificationEvidenceGateMod()):
        kernel.register_mod(mod, granted_capabilities=set(mod.required_capabilities))
    register_autonomous_controllers(kernel)


def register_autonomous_controllers(kernel: ModKernel) -> None:
    """Register the autonomous controller mods (BotModeMod, TaskRouterMod, FailureSentinelMod)."""
    from alpha.mods.controllers.bot_mode_mod import BotModeMod
    from alpha.mods.controllers.failure_sentinel_mod import FailureSentinelMod
    from alpha.mods.controllers.task_router_mod import TaskRouterMod

    for mod in (BotModeMod(), TaskRouterMod(), FailureSentinelMod()):
        kernel.register_mod(mod, granted_capabilities=set(mod.required_capabilities))


async def require_mod_admission(kernel: ModKernel, event: AlphaEvent) -> EventResult:
    """Admit only an affirmative CONTINUE; kernel faults and other outcomes refuse."""
    try:
        result = await kernel.dispatch(event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise ModAdmissionError(f"Mod Kernel could not evaluate {event.name}; operation refused") from exc
    if result.outcome not in (EventOutcome.CONTINUE, EventOutcome.OBSERVE):
        reason = result.reason or result.outcome.value
        raise ModAdmissionError(f"Mod Kernel returned {result.outcome.value} for {event.name}: {reason}")
    return result


def sync_dispatch(kernel: ModKernel, event: AlphaEvent, timeout: float = 10.0) -> EventResult:
    """Execute kernel.dispatch synchronously, safe whether an event loop is running or not."""
    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if not in_loop:
        return asyncio.run(kernel.dispatch(event))

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, kernel.dispatch(event))
        return future.result(timeout=timeout)
