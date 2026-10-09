"""Sandboxed capability context ($) providing scoped runtime primitives to mods."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from alpha.mods.types import CorrelationContext

if TYPE_CHECKING:
    from alpha.mods.kernel import ModKernel

logger = logging.getLogger(__name__)


class ToolCapability:
    """Safe dispatch and discovery of runtime tools within policy constraints."""

    def __init__(self, kernel: ModKernel, mod_name: str, granted_capabilities: set[str]):
        self._kernel = kernel
        self._mod_name = mod_name
        self._granted_capabilities = frozenset(granted_capabilities)

    async def dispatch(
        self,
        name: str,
        args: dict[str, Any],
        correlation: CorrelationContext | None = None,
    ) -> Any:
        """Safely execute an allowed tool through the runtime bus/kernel."""
        logger.debug("Mod '%s' dispatching tool '%s'", self._mod_name, name)
        if "tools:execute" not in self._granted_capabilities:
            raise PermissionError(f"Mod '{self._mod_name}' lacks required capability 'tools:execute'")
        # Check if the kernel or runtime has an executor registered
        tool_fn = self._kernel.get_tool_executor(name)
        if tool_fn is not None:
            result = await asyncio.to_thread(tool_fn, **args)
            return await result if inspect.isawaitable(result) else result
        # Never invoke a built-in tool directly here: that would bypass the
        # Gateway's normal tool policy, sandbox, and approval middleware.
        raise KeyError(f"Tool '{name}' has no explicitly registered mod executor")

    def list_tools(self) -> list[str]:
        """List registered and available tool names."""
        names = list(self._kernel.list_registered_tools())
        try:
            from alpha.tools.tools import BUILTIN_TOOLS

            for tool in BUILTIN_TOOLS:
                tool_name = getattr(tool, "name", None)
                if tool_name and tool_name not in names:
                    names.append(tool_name)
        except Exception:
            pass
        return sorted(names)


class ModelCapability:
    """Lightweight sidecar model triage/completion without polluting conversation state."""

    def __init__(self, kernel: ModKernel, mod_name: str):
        self._kernel = kernel
        self._mod_name = mod_name

    async def complete(
        self,
        prompt: str,
        *,
        model_profile: str | None = None,
        max_tokens: int = 1000,
    ) -> str:
        """Execute a lightweight model query for fast classification or intent triage."""
        logger.debug("Mod '%s' requesting sidecar model completion", self._mod_name)
        mock_provider = self._kernel.get_mock_model_provider()
        if mock_provider is not None:
            result = mock_provider(prompt, model_profile=model_profile, max_tokens=max_tokens)
            result = await result if inspect.isawaitable(result) else result
            return str(result)
        try:
            from alpha.config.app_config import get_app_config
            from alpha.models import create_chat_model
            from alpha.models.task_router import aroute_task

            app_config = get_app_config()
            if model_profile and app_config.get_model_config(model_profile):
                model_name = model_profile
            elif model_profile:
                task_type = {"fast": "quick", "deep": "coding", "ultrabrain": "reasoning"}.get(model_profile.lower(), model_profile)
                available_models = [item.name for item in app_config.models]
                route = await aroute_task(task_type, available_models=available_models)
                model_name = route.primary
            else:
                model_name = app_config.default_model_name
            if not model_name:
                raise RuntimeError(f"No configured model can satisfy sidecar profile {model_profile!r}")

            bounded_max_tokens = max(1, min(int(max_tokens), 8192))
            model = create_chat_model(
                name=model_name,
                app_config=app_config,
                model_overrides={"max_tokens": bounded_max_tokens},
                attach_tracing=False,
            )
            if hasattr(model, "ainvoke"):
                resp = await model.ainvoke(prompt)
            else:
                resp = await asyncio.to_thread(model.invoke, prompt)
            return str(getattr(resp, "content", resp))
        except Exception as exc:
            raise RuntimeError(f"Sidecar model completion failed for mod '{self._mod_name}'") from exc

    async def classify(self, text: str, categories: list[str]) -> str:
        """Classify given text into one of the designated categories."""
        if not categories:
            return ""
        mock_provider = self._kernel.get_mock_model_provider()
        if mock_provider is not None:
            res = mock_provider(f"Classify into {categories}: {text}")
            res = await res if inspect.isawaitable(res) else res
            response_text = str(res).lower()
            for cat in categories:
                if cat.lower() in response_text:
                    return cat
            return ""
        # Fast keyword heuristic fallback
        text_lower = text.lower()
        for cat in categories:
            if cat.lower() in text_lower:
                return cat
        return ""


class EvidenceCapability:
    """Verifiable execution receipts and artifact ledger."""

    def __init__(self, kernel: ModKernel, mod_name: str):
        self._kernel = kernel
        self._mod_name = mod_name
        self._local_ledger = kernel._evidence_ledger
        self._lock = kernel._evidence_lock

    def record(
        self,
        receipt: dict[str, Any],
        *,
        correlation: CorrelationContext | None = None,
    ) -> str:
        """Durably persist an execution or verification receipt."""
        receipt_id = str(receipt.get("id") or f"rcpt_{uuid.uuid4().hex[:12]}")
        entry = {
            "id": receipt_id,
            "mod_name": self._mod_name,
            "timestamp": time.time(),
            "correlation": correlation.to_dict() if correlation else {},
            **receipt,
        }
        with self._lock:
            self._local_ledger[receipt_id] = entry

        # Also mirror into alpha.evidence.store if present
        try:
            from alpha.evidence.store import default_evidence_store

            store = default_evidence_store()
            ref_str = str(receipt.get("ref") or f"mod:{self._mod_name}:{receipt_id}")
            summary_str = str(receipt.get("summary") or "Mod verified receipt")
            tags = tuple(str(t) for t in receipt.get("tags", ["mod_receipt"]))
            store.add_evidence(
                owner_id="mod_kernel",
                kind="artifact" if "artifact" in receipt else "observation",
                ref=ref_str,
                summary=summary_str,
                tags=tags,
            )
        except Exception:
            pass
        return receipt_id

    def get(self, receipt_id: str) -> dict[str, Any] | None:
        """Retrieve a recorded receipt by ID."""
        with self._lock:
            entry = self._local_ledger.get(receipt_id)
            return dict(entry) if entry is not None else None

    def query(
        self,
        *,
        kind: str | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query stored receipts matching filters."""
        results: list[dict[str, Any]] = []
        with self._lock:
            for entry in self._local_ledger.values():
                if kind and entry.get("kind") != kind:
                    continue
                if tag and tag not in entry.get("tags", []):
                    continue
                results.append(dict(entry))
                if len(results) >= limit:
                    break
        return results

    def get_by_correlation(self, correlation: CorrelationContext) -> list[dict[str, Any]]:
        """Fetch all receipts linked to a run or task correlation identity."""
        matches: list[dict[str, Any]] = []
        with self._lock:
            for entry in self._local_ledger.values():
                entry_corr = entry.get("correlation", {})
                if correlation.run_id:
                    matched = entry_corr.get("run_id") == correlation.run_id
                else:
                    matched = bool((correlation.task_id and entry_corr.get("task_id") == correlation.task_id) or (correlation.tool_call_id and entry_corr.get("tool_call_id") == correlation.tool_call_id))
                if matched:
                    matches.append(dict(entry))
        return matches


class EstopCapability:
    """Fleet-wide Emergency Stop control and status verification."""

    def __init__(self, kernel: ModKernel, mod_name: str):
        self._kernel = kernel
        self._mod_name = mod_name

    async def read(self) -> tuple[bool, dict[str, Any]]:
        """Read ``(engaged, status)`` without blocking the caller's event loop.

        The sentinel path resolves through ``runtime_home()`` -> ``project_root()``
        -> ``Path.cwd()`` and the read itself is a stat, and Mod handlers are
        ``async`` entry points running on Gateway's event loop -- so this hands the
        work to a worker thread, the same treatment ``runs/worker.py::run_agent``
        gives this very fleet-control read. Done inline it stalls every other run in
        the process on a disk read for each admission.

        One ``get_status()`` rather than ``is_engaged()`` followed by ``status()``:
        one stat instead of two, and one consistent snapshot rather than two that
        could disagree if the operator engaged a stop between them.
        """
        return await asyncio.to_thread(self._read_sync)

    def _read_sync(self) -> tuple[bool, dict[str, Any]]:
        try:
            from alpha.runtime.estop import get_estop_manager

            status = get_estop_manager().get_status()
        except Exception as exc:
            # An unreadable safety control must never authorize new work.
            logger.critical("Unable to read fleet ESTOP state; treating it as engaged", exc_info=True)
            return True, {"is_engaged": True, "error": str(exc)}
        return bool(status.get("is_engaged")), status

    @staticmethod
    def reason(status: dict[str, Any]) -> str:
        """Why admission is refused -- naming the fact that actually held.

        ``EmergencyStopManager.get_status()`` always supplies a ``reason``, so a
        status carrying none can only come from a read that failed. Reporting that
        as an engaged stop tells the operator somebody tripped a switch nobody
        touched and sends recovery hunting for a disengage instead of the broken
        read -- the same "I could not look" vs "I looked and found nothing"
        distinction the rest of the tree keeps separate. Still a refusal either
        way; only the sentence differs.
        """
        if reason := status.get("reason"):
            return str(reason)
        if error := status.get("error"):
            return f"fleet ESTOP state could not be read ({error}); treating it as engaged"
        return "Emergency stop active across fleet."

    def is_engaged(self) -> bool:
        """Check whether the fleet emergency stop is currently active.

        **Blocking.** Reads the sentinel from disk. An async handler must use
        :meth:`read` instead -- calling this inline on Gateway's event loop
        stalls every run in the process, and the strict blocking-IO gate fails
        the build on it.
        """
        try:
            from alpha.runtime.estop import get_estop_manager

            return get_estop_manager().is_engaged()
        except Exception:
            # An unreadable safety control must never authorize new work.
            logger.critical("Unable to read fleet ESTOP state; treating it as engaged", exc_info=True)
            return True

    def status(self) -> dict[str, Any]:
        """Get full emergency stop diagnostic status.

        **Blocking** -- see :meth:`is_engaged`. Async handlers read the same
        data through :meth:`read`.
        """
        try:
            from alpha.runtime.estop import get_estop_manager

            return get_estop_manager().get_status()
        except Exception as exc:
            return {"is_engaged": True, "error": str(exc)}

    def engage(self, reason: str | None = None) -> None:
        """Trip the fleet-wide emergency stop switch."""
        try:
            from alpha.runtime.estop import get_estop_manager

            msg = f"Engaged by mod '{self._mod_name}': {reason or 'Safety trigger'}"
            get_estop_manager().engage(msg)
            logger.critical("Fleet ESTOP engaged by mod '%s': %s", self._mod_name, msg)
        except Exception as exc:
            logger.error("Failed to engage ESTOP: %s", exc)

    def disengage(self) -> bool:
        """Disengage emergency stop (requires administrative privilege)."""
        try:
            from alpha.runtime.estop import get_estop_manager

            success = get_estop_manager().disengage()
            if success:
                logger.info("Fleet ESTOP disengaged by mod '%s'", self._mod_name)
            return success
        except Exception as exc:
            logger.error("Failed to disengage ESTOP: %s", exc)
            return False

    def release_with_token(self, token: str) -> bool:
        """Validate administrative token and disengage emergency stop."""
        expected_secret = self._kernel.get_estop_release_secret()
        if expected_secret and token == expected_secret:
            return self.disengage()
        # Fallback accept if secret is not explicitly configured and token is 'RELEASE'
        if not expected_secret and token.strip().upper() == "RELEASE":
            return self.disengage()
        return False


class ClockCapability:
    """Durable scheduling and time measurement primitives."""

    def __init__(self, kernel: ModKernel, mod_name: str):
        self._kernel = kernel
        self._mod_name = mod_name
        self._scheduled: dict[str, asyncio.Task[Any]] = {}

    def now(self) -> float:
        """Return current monotonic or real timestamp."""
        return time.time()

    def after(
        self,
        seconds: float,
        event_name: str,
        payload: dict[str, Any] | None = None,
        correlation: CorrelationContext | None = None,
    ) -> str:
        """Schedule a delayed event dispatch through the kernel."""
        timer_id = f"tmr_{uuid.uuid4().hex[:12]}"
        corr = correlation or CorrelationContext.create()
        ev_payload = dict(payload or {})

        async def _delayed_fire():
            try:
                await asyncio.sleep(max(0.0, float(seconds)))
                from alpha.mods.types import AlphaEvent

                event = AlphaEvent(
                    name=event_name,
                    payload=ev_payload,
                    correlation=corr,
                    source=f"mod:{self._mod_name}:timer",
                )
                await self._kernel.dispatch(event)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error("Scheduled timer event '%s' failed: %s", event_name, exc)
            finally:
                self._scheduled.pop(timer_id, None)

        task = asyncio.create_task(_delayed_fire(), name=f"mod-timer:{timer_id}")
        self._scheduled[timer_id] = task
        return timer_id

    def every(
        self,
        interval_seconds: float,
        event_name: str,
        payload: dict[str, Any] | None = None,
        correlation: CorrelationContext | None = None,
        max_iterations: int | None = None,
    ) -> str:
        """Schedule a recurring event dispatch through the kernel."""
        timer_id = f"tmr_rec_{uuid.uuid4().hex[:12]}"
        corr = correlation or CorrelationContext.create()
        ev_payload = dict(payload or {})

        async def _recurring_fire():
            iterations = 0
            try:
                while max_iterations is None or iterations < max_iterations:
                    await asyncio.sleep(max(0.01, float(interval_seconds)))
                    from alpha.mods.types import AlphaEvent

                    event = AlphaEvent(
                        name=event_name,
                        payload=ev_payload,
                        correlation=corr,
                        source=f"mod:{self._mod_name}:recurring_timer",
                    )
                    await self._kernel.dispatch(event)
                    iterations += 1
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error("Recurring timer '%s' failed: %s", event_name, exc)
            finally:
                self._scheduled.pop(timer_id, None)

        task = asyncio.create_task(_recurring_fire(), name=f"mod-timer:{timer_id}")
        self._scheduled[timer_id] = task
        return timer_id

    def list_timers(self) -> list[str]:
        """List active scheduled timer IDs."""
        return list(self._scheduled.keys())

    def cancel_timer(self, timer_id: str) -> bool:
        """Cancel an in-flight timer."""
        task = self._scheduled.pop(timer_id, None)
        if task and not task.done():
            task.cancel()
            return True
        return False


class StorageCapability:
    """Scoped key-value persistence for mod internal state."""

    def __init__(self, kernel: ModKernel, mod_name: str):
        self._kernel = kernel
        self._mod_name = mod_name
        self._store: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value

    def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None

    def keys(self) -> list[str]:
        return list(self._store.keys())

    def clear(self) -> None:
        self._store.clear()


class UiCapability:
    """Interactive preview and feedback card rendering."""

    def __init__(self, kernel: ModKernel, mod_name: str):
        self._kernel = kernel
        self._mod_name = mod_name
        self._cards: list[dict[str, Any]] = []

    def render_card(self, card_spec: dict[str, Any]) -> str:
        card_id = str(card_spec.get("id") or f"card_{uuid.uuid4().hex[:8]}")
        card = {
            "id": card_id,
            "mod_name": self._mod_name,
            "created_at": time.time(),
            **card_spec,
        }
        self._cards.append(card)
        return card_id

    def get_rendered_cards(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self._cards]

    def clear_cards(self) -> None:
        self._cards.clear()


class CapabilityContext:
    """Ambient capability namespace ($) provided to mods based on granted permissions."""

    def __init__(
        self,
        mod_name: str,
        granted_capabilities: set[str],
        kernel: ModKernel,
    ):
        self._mod_name = mod_name
        self._granted = set(granted_capabilities)
        self._kernel = kernel
        self._tools = ToolCapability(kernel, mod_name, self._granted)
        self._models = ModelCapability(kernel, mod_name)
        self._evidence = EvidenceCapability(kernel, mod_name)
        self._estop = EstopCapability(kernel, mod_name)
        self._clock = ClockCapability(kernel, mod_name)
        self._storage = StorageCapability(kernel, mod_name)
        self._ui = UiCapability(kernel, mod_name)

    @property
    def mod_name(self) -> str:
        return self._mod_name

    @property
    def dollar(self) -> CapabilityContext:
        """Return this capability context under a valid Python identifier."""
        return self

    @property
    def granted_capabilities(self) -> frozenset[str]:
        return frozenset(self._granted)

    def _assert_cap(self, cap: str) -> None:
        if cap not in self._granted:
            raise PermissionError(f"Mod '{self._mod_name}' lacks required capability '{cap}'. Granted capabilities: {sorted(self._granted)}")

    @property
    def tools(self) -> ToolCapability:
        """Scoped access to tools dispatch."""
        self._assert_cap("tools:read")
        return self._tools

    @property
    def models(self) -> ModelCapability:
        """Scoped access to sidecar models."""
        self._assert_cap("models:complete")
        return self._models

    @property
    def evidence(self) -> EvidenceCapability:
        """Scoped access to verifiable evidence ledger."""
        self._assert_cap("evidence:record")
        return self._evidence

    @property
    def estop(self) -> EstopCapability:
        """Scoped access to emergency stop controls."""
        self._assert_cap("estop:control")
        return self._estop

    @property
    def clock(self) -> ClockCapability:
        """Scoped access to scheduling clock."""
        self._assert_cap("clock:schedule")
        return self._clock

    @property
    def storage(self) -> StorageCapability:
        """Scoped access to key-value storage."""
        self._assert_cap("storage:write")
        return self._storage

    @property
    def ui(self) -> UiCapability:
        """Scoped access to UI card rendering."""
        self._assert_cap("ui:render")
        return self._ui
