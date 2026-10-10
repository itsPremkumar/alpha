"""The Alpha Mod Kernel (AMK) — deterministic ordered middleware execution engine."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from collections.abc import Callable
from contextvars import ContextVar
from threading import RLock
from typing import Any

from alpha.mods.commands import ModCommandRegistry
from alpha.mods.context import MAX_KERNEL_CARDS as MAX_UI_CARDS
from alpha.mods.context import CapabilityContext
from alpha.mods.state import ModStateStore
from alpha.mods.types import (
    AlphaEvent,
    AlphaMod,
    EventOutcome,
    EventResult,
    ModPriority,
    NextHandler,
)

logger = logging.getLogger(__name__)

#: The ordered list of mods participating in the dispatch currently running in
#: this task. A *list* rather than a tuple because the kernel builds the chain
#: incrementally as each handler is entered, and because a continuation handed
#: to ``asyncio.create_task`` copies the context: the child task sees the same
#: list object, so downstream handlers still record themselves.
_active_chain: ContextVar[list[dict[str, Any]]] = ContextVar("alpha_mod_dispatch_chain")


class ModAdmissionError(RuntimeError):
    """A policy gate could not affirmatively admit a protected operation."""


class ModRegistrationError(RuntimeError):
    """A registration guard refused a mod that would weaken the control chain."""


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
        # UI cards mods render, retained on the kernel so an operator can see
        # (and act on) a hold after the event that produced it has ended.
        self._ui_cards: list[dict[str, Any]] = []
        # Kernel-owned shared state and mod commands. These live here rather
        # than on a CapabilityContext because a context is rebuilt per event:
        # per-context storage loses everything it recorded the moment the event
        # ends, which makes cross-handler state impossible.
        self._state = ModStateStore()
        self._commands = ModCommandRegistry()
        self._registration_guards: list[Callable[[AlphaMod, set[str]], str | None]] = []

    # -- shared runtime services ------------------------------------------

    @property
    def state(self) -> ModStateStore:
        """The kernel-owned, mod-namespaced state store."""
        return self._state

    @property
    def commands(self) -> ModCommandRegistry:
        """The registry of commands mods contributed at runtime."""
        return self._commands

    def record_ui_card(self, card: dict[str, Any]) -> None:
        """Retain a card a mod rendered, bounded oldest-first."""
        self._ui_cards.append(dict(card))
        overflow = len(self._ui_cards) - MAX_UI_CARDS
        if overflow > 0:
            del self._ui_cards[:overflow]

    def list_ui_cards(self, *, mod_name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        cards = list(self._ui_cards)
        if mod_name is not None:
            cards = [c for c in cards if c.get("mod_name") == mod_name]
        return [dict(c) for c in cards[-max(0, int(limit)) :]]

    def clear_ui_cards(self, mod_name: str | None = None) -> int:
        if mod_name is None:
            count = len(self._ui_cards)
            self._ui_cards.clear()
            return count
        kept = [c for c in self._ui_cards if c.get("mod_name") != mod_name]
        removed = len(self._ui_cards) - len(kept)
        self._ui_cards = kept
        return removed

    def add_registration_guard(self, guard: Callable[[AlphaMod, set[str]], str | None]) -> None:
        """Install a guard consulted before any mod is registered.

        A guard returns ``None`` to admit the mod, or a ``str`` reason to refuse
        it. Guards run in installation order, so the first one installed is the
        one with final say — the onion property Claude Code gives whichever mod
        an admin prepends.
        """
        self._registration_guards.append(guard)

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

        for guard in list(self._registration_guards):
            reason = guard(mod, caps)
            if reason:
                raise ModRegistrationError(f"Registration of mod '{mod.name}' refused: {reason}")

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
        # A mod that leaves takes its commands and its state with it, or a
        # re-registered replacement would inherit a stranger's command names.
        try:
            self._commands.unregister_mod(name)
        except Exception:  # pragma: no cover - defensive
            logger.debug("Failed dropping commands for mod '%s'", name)
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
        return CapabilityContext(mod.name, caps, self, state_store=self._state)

    async def dispatch(
        self,
        event: AlphaEvent,
        terminal_handler: NextHandler | None = None,
    ) -> EventResult:
        """Executes the ordered middleware pipeline for the given event."""
        start_time = time.time()
        matching_mods = [m for m in self._mods if self._matches_event(m, event.name)]

        # The control chain is per-dispatch state, not per-mod state: the audit
        # ledger and the security-default guard both need to see *which* mod
        # answered, which only exists once the whole chain has run. It is
        # attached to the event so any handler can read the live list.
        chain_token = _active_chain.set([])
        try:
            event._mod_chain = _active_chain.get()
        except Exception:  # pragma: no cover - AlphaEvent is a plain dataclass
            pass

        async def _default_terminal(ev: AlphaEvent) -> EventResult:
            return EventResult.continue_(ev)

        terminal = terminal_handler or _default_terminal

        def _record(mod: AlphaMod) -> dict[str, Any] | None:
            chain = _active_chain.get(None)
            if chain is None:
                return None
            entry = {"mod": mod.name, "priority": int(getattr(mod, "priority", 0)), "event": event.name, "outcome": None}
            chain.append(entry)
            return entry

        async def _compose(index: int, current_event: AlphaEvent) -> EventResult:
            if index >= len(matching_mods):
                return await terminal(current_event)

            mod = matching_mods[index]
            ctx = self._create_context(mod)
            entry = _record(mod)

            next_called = False
            next_task: asyncio.Task[EventResult] | None = None

            async def _next(next_ev: AlphaEvent) -> EventResult:
                nonlocal next_called, next_task
                if next_task is None:
                    next_called = True
                    # A handler may accidentally await next() more than once or
                    # issue concurrent continuations. Share one downstream
                    # execution so a tool side effect cannot be duplicated.
                    next_task = asyncio.create_task(_compose(index + 1, next_ev))
                elif next_ev.event_id != current_event.event_id or next_ev.name != current_event.name:
                    logger.warning(
                        "Mod '%s' attempted to continue event %s more than once with a different event; reusing the first result",
                        mod.name,
                        current_event.event_id,
                    )
                return await next_task

            try:
                res = await mod.handle(ctx, current_event, _next)
                if entry is not None:
                    entry["outcome"] = res.outcome.value if hasattr(res, "outcome") else str(type(res).__name__)
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

        try:
            final_result = await _compose(0, event)
        finally:
            chain = _active_chain.get(None)
            _active_chain.reset(chain_token)
        duration = time.time() - start_time
        self._record_journal(
            event=event,
            outcome=final_result.outcome,
            reason=final_result.reason,
            duration=duration,
            mod_chain=list(chain or []),
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
        mod_chain: list[dict[str, Any]] | None = None,
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
        if mod_chain:
            entry["mod_chain"] = mod_chain
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

    # -- introspection -----------------------------------------------------

    def control_chain(self) -> list[dict[str, Any]]:
        """The ordered chain of mods as the kernel would run them.

        This is the auditable answer to "who runs first, and can anything
        outrank the safety triad?". Registration order alone does not answer
        it — priority does — so this is sorted by the same key ``dispatch``
        uses, and never by insertion time.
        """
        return [
            {
                "order": idx,
                "mod": m.name,
                "version": str(getattr(m, "version", "1.0.0")),
                "priority": int(getattr(m, "priority", ModPriority.USER_EXTENSIONS)),
                "first_party": str(getattr(m.__class__, "__module__", "")).startswith("alpha.mods."),
                "subscribed_events": (["*"] if getattr(m, "subscribed_events", None) is None else sorted({str(s) for s in getattr(m, "subscribed_events", [])})),
            }
            for idx, m in enumerate(self._mods)
        ]

    def describe_mods(self) -> list[dict[str, Any]]:
        """Review-facing description of every registered mod.

        Nothing here executes a mod. It reports what the kernel already knows —
        its subscription set, its granted capabilities, and any declared
        manifest — plus the state keys it has actually touched in this process.
        """
        out: list[dict[str, Any]] = []
        for mod in self._mods:
            name = str(getattr(mod, "name", mod.__class__.__name__))
            try:
                from alpha.mods.manifest import describe_mod

                declared = self._state.declaration(name)
                observed = self._state.observed(name)
                out.append(
                    describe_mod(
                        mod,
                        granted_capabilities=self._capabilities.get(name, set()),
                        declared_state=declared,
                        observed_state=observed,
                    ).to_dict()
                )
            except Exception as exc:  # pragma: no cover - describe() must not break the API
                out.append({"name": name, "error": f"description unavailable: {exc}"})
        return out

    def find_outcome_owner(self, outcome: EventOutcome) -> str | None:
        """Name the mod in the active chain that produced ``outcome``.

        The *last* handler returning an outcome owns it: in an onion chain a
        wrapper that awaits ``next`` returns whatever the downstream produced, so
        the deepest handler is the one that actually decided. Reading only the
        first match would attribute a downstream decision to the outermost mod.
        """
        chain = _active_chain.get(None)
        if not chain:
            return None
        wanted = outcome.value if hasattr(outcome, "value") else str(outcome)
        owner: str | None = None
        for entry in chain:
            if entry.get("outcome") == wanted:
                owner = str(entry.get("mod"))
        return owner


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
    """Register first-party safety enforcers and autonomous controllers by default.

    Order is the contract. The security-default guard is installed **before**
    any mod is registered, because it is the thing that decides which later
    registrations are allowed; the fleet ESTOP circuit breaker then registers
    first among mods so nothing can outrank it. Claude Code gets this from
    ``sec-default`` loading first; here it is explicit in this function.
    """
    from alpha.mods.audit import AuditLedgerMod
    from alpha.mods.context_budget import ContextBudgetMod
    from alpha.mods.enforcers.blast_radius_mod import BlastRadiusGuardMod
    from alpha.mods.enforcers.estop_mod import FleetEstopMod
    from alpha.mods.enforcers.verification_gate_mod import VerificationEvidenceGateMod
    from alpha.mods.replay import ReplayMod
    from alpha.mods.sec_default import SecDefaultMod

    guard = SecDefaultMod()
    guard.attach(kernel)
    kernel.add_registration_guard(guard.guard_registration)
    # The guard is a real mod as well as a registration gate, and it is
    # registered first so its `handle` is the outermost wrapper in the chain.
    kernel.register_mod(guard, granted_capabilities=set(guard.required_capabilities))

    # The audit ledger is registered second, also in the KERNEL tier, so stable
    # ordering puts it directly inside the guard and outside everything else.
    audit = AuditLedgerMod()
    kernel.register_mod(audit, granted_capabilities=set(audit.required_capabilities))

    triad: list[Any] = [FleetEstopMod(), BlastRadiusGuardMod(), VerificationEvidenceGateMod()]
    for mod in triad:
        kernel.register_mod(mod, granted_capabilities=set(mod.required_capabilities))
    register_autonomous_controllers(kernel)

    # Observability and budget mods. They run after the safety triad so a budget
    # can never outrank a security refusal, and the replay recorder runs after
    # that because it only needs to observe writes that were admitted.
    for mod in (ContextBudgetMod(), ReplayMod()):
        kernel.register_mod(mod, granted_capabilities=set(mod.required_capabilities))


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
