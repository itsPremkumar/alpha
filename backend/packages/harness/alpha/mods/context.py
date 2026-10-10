"""Sandboxed capability context ($) providing scoped runtime primitives to mods."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from alpha.mods.commands import ModCommand
from alpha.mods.state import ModStateStore
from alpha.mods.types import CorrelationContext

if TYPE_CHECKING:
    from alpha.mods.kernel import ModKernel

logger = logging.getLogger(__name__)

#: How many rendered cards the kernel retains for operator inspection. Cards are
#: a projection, not a message bus: an unbounded list is a slow leak in a
#: long-lived Gateway.
MAX_KERNEL_CARDS = 200

#: File names a mod may never read through ``$.fs`` regardless of path. A mod
#: that wants a credential has no legitimate reason to want *this* file.
_FORBIDDEN_FILE_PATTERN = re.compile(
    r"(^|[\\/])\.env(\.[^\\/]+)?$|(^|[\\/])id_rsa$|(^|[\\/])id_ed25519$|(^|[\\/])\.ssh([\\/]|$)|"
    r"(^|[\\/])\.aws([\\/]|$)|(^|[\\/])credentials(\.[^\\/]+)?$|\.(pem|key|p12|pfx|jks)$|(^|[\\/])secrets?\.(ya?ml|json)$",
    re.IGNORECASE,
)

#: Roots outside every sandbox a mod could legitimately reason about.
_FORBIDDEN_ROOTS = (
    "/etc/",
    "/usr/",
    "/var/",
    "/bin/",
    "/sbin/",
    "/boot/",
    "/root/",
    "/proc/",
    "/sys/",
)

_WINDOWS_FORBIDDEN_ROOTS = (
    "c:\\windows",
    "c:\\program files",
    "c:\\program files (x86)",
    "c:\\programdata",
)


def _is_forbidden_path(path: str) -> str | None:
    """Return a reason when ``path`` is outside a mod's reach, else ``None``."""
    raw = str(path or "").strip()
    if not raw:
        return "empty path"
    if _FORBIDDEN_FILE_PATTERN.search(raw):
        return "credential-shaped file"
    lowered = raw.lower()
    for root in _FORBIDDEN_ROOTS:
        if lowered.startswith(root):
            return f"system root '{root}'"
    for root in _WINDOWS_FORBIDDEN_ROOTS:
        if lowered.startswith(root):
            return f"system root '{root}'"
    return None


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

    def is_engaged(self) -> bool:
        """Check whether the fleet emergency stop is currently active."""
        try:
            from alpha.runtime.estop import get_estop_manager

            return get_estop_manager().is_engaged()
        except Exception:
            # An unreadable safety control must never authorize new work.
            logger.critical("Unable to read fleet ESTOP state; treating it as engaged", exc_info=True)
            return True

    def status(self) -> dict[str, Any]:
        """Get full emergency stop diagnostic status."""
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
    """Scoped key-value persistence for mod internal state.

    The store is the kernel's, not this object's. A ``CapabilityContext`` is
    rebuilt for every dispatched event, so a dict held here would lose
    everything it recorded the moment the event ended — one hook could not pass
    a value to another, and a counter would reset on the next tool call. The
    namespace is still per-mod: ``clear()`` only ever empties *this* mod's keys.

    Read and write are separate capabilities. A read-only mod (``storage:read``)
    can observe its own namespace without being able to mutate it, and a
    write-granted mod can still read what it wrote — write implies read, the
    convention that lets a counter mod do ``get`` then ``set`` under a single
    ``storage:write`` grant.
    """

    def __init__(self, kernel: ModKernel, mod_name: str, store: ModStateStore, granted: set[str] | frozenset[str] | None = None):
        self._kernel = kernel
        self._mod_name = mod_name
        self._store = store
        self._granted = set(granted or ())

    def _require_any(self, *caps: str) -> None:
        if not any(cap in self._granted for cap in caps):
            raise PermissionError(f"Mod '{self._mod_name}' lacks required capability '{caps[0]}'. Granted capabilities: {sorted(self._granted)}")

    def _require_write(self) -> None:
        self._require_any("storage:write")

    def get(self, key: str, default: Any = None) -> Any:
        self._require_any("storage:read", "storage:write")
        return self._store.get(self._mod_name, key, default)

    def set(self, key: str, value: Any) -> None:
        self._require_write()
        self._store.set(self._mod_name, key, value)

    def delete(self, key: str) -> bool:
        self._require_write()
        return self._store.delete(self._mod_name, key)

    def keys(self) -> list[str]:
        self._require_any("storage:read", "storage:write")
        return self._store.keys(self._mod_name)

    def clear(self) -> int:
        self._require_write()
        return self._store.clear(self._mod_name)

    def snapshot(self) -> dict[str, Any]:
        self._require_any("storage:read", "storage:write")
        return self._store.snapshot(self._mod_name)


@dataclass(frozen=True)
class FileSnapshot:
    """A bounded, read-only view of a file a mod asked to inspect.

    ``available`` is the honest field. A mod that wanted a file's previous
    contents to build a diff needs to know the read *failed*, and must never be
    handed an empty string that looks like "the file was empty" — that is a
    fabricated before-state for a replay.
    """

    path: str
    available: bool
    text: str = ""
    size: int = 0
    truncated: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "available": self.available,
            "size": self.size,
            "truncated": self.truncated,
            "reason": self.reason,
        }


class FileCapability:
    """Read-only, confined filesystem access for mods that need real content.

    Claude Code's blast-radius mod dry-runs ``git status`` and its replay mod
    reads a file's old contents before a write lands. Both need the same thing:
    to *look* without being able to *change*. This capability therefore only
    reads, and only inside a confined envelope:

    - credential-shaped names and system roots are refused by path pattern,
      before any ``open()`` happens, because a mod that wants ``.env`` has no
      legitimate reason to want that file;
    - reads are bounded, so a 4 GB log cannot be pulled into a pipeline;
    - an unreadable or forbidden path returns ``available=False`` with a reason
      rather than raising, because "I could not look" and "the file was empty"
      are opposite findings.
    """

    def __init__(self, kernel: ModKernel, mod_name: str):
        self._kernel = kernel
        self._mod_name = mod_name

    def read_text(self, path: str, *, max_bytes: int = 256 * 1024) -> FileSnapshot:
        """Read at most ``max_bytes`` of ``path`` as text."""
        raw = str(path or "").strip()
        forbidden = _is_forbidden_path(raw)
        if forbidden:
            return FileSnapshot(path=raw, available=False, reason=f"refused: {forbidden}")
        try:
            from pathlib import Path

            target = Path(raw)
            if not target.is_file():
                return FileSnapshot(path=raw, available=False, reason="not a regular file")
            size = target.stat().st_size
            with target.open("rb") as fh:
                data = fh.read(max(1, int(max_bytes)))
            return FileSnapshot(
                path=raw,
                available=True,
                text=data.decode("utf-8", errors="replace"),
                size=int(size),
                truncated=bool(size > len(data)),
                reason="",
            )
        except OSError as exc:
            return FileSnapshot(path=raw, available=False, reason=f"unreadable: {exc}")

    def exists(self, path: str) -> bool:
        raw = str(path or "").strip()
        if _is_forbidden_path(raw):
            return False
        try:
            from pathlib import Path

            return Path(raw).exists()
        except OSError:
            return False

    def size(self, path: str) -> int | None:
        """Byte size of a file, or ``None`` when it cannot be measured."""
        raw = str(path or "").strip()
        if _is_forbidden_path(raw):
            return None
        try:
            from pathlib import Path

            target = Path(raw)
            return int(target.stat().st_size) if target.is_file() else None
        except OSError:
            return None


class CommandCapability:
    """Lets a mod contribute a ``/command`` that runs without a model turn."""

    def __init__(self, kernel: ModKernel, mod_name: str):
        self._kernel = kernel
        self._mod_name = mod_name

    def register(
        self,
        name: str,
        handler: Any,
        *,
        description: str = "",
        requires_approval: bool = False,
    ) -> ModCommand:
        return self._kernel.commands.register(
            self._mod_name,
            name,
            handler,
            description=description,
            requires_approval=requires_approval,
        )

    def unregister(self, name: str) -> bool:
        return self._kernel.commands.unregister(self._mod_name, name)

    def list_commands(self) -> list[ModCommand]:
        return self._kernel.commands.list_commands(self._mod_name)


class UiCapability:
    """Interactive preview and feedback card rendering.

    Cards are recorded on the kernel so an operator can see what a mod is
    holding, not just on this per-event context — a hold card that vanishes when
    the event ends is a hold nobody can approve.
    """

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
        self._kernel.record_ui_card(card)
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
        *,
        state_store: ModStateStore | None = None,
    ):
        self._mod_name = mod_name
        self._granted = set(granted_capabilities)
        self._kernel = kernel
        self._state_store = state_store or getattr(kernel, "state", None) or ModStateStore()
        self._tools = ToolCapability(kernel, mod_name, self._granted)
        self._models = ModelCapability(kernel, mod_name)
        self._evidence = EvidenceCapability(kernel, mod_name)
        self._estop = EstopCapability(kernel, mod_name)
        self._clock = ClockCapability(kernel, mod_name)
        self._storage = StorageCapability(kernel, mod_name, self._state_store, self._granted)
        self._ui = UiCapability(kernel, mod_name)
        self._commands = CommandCapability(kernel, mod_name)
        self._fs = FileCapability(kernel, mod_name)

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

    def _assert_any_cap(self, *caps: str) -> None:
        if not any(cap in self._granted for cap in caps):
            raise PermissionError(f"Mod '{self._mod_name}' lacks required capability '{' or '.join(caps)}'. Granted capabilities: {sorted(self._granted)}")

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
        """Scoped access to kernel-owned mod state.

        Read and write are distinct capabilities: a read-only mod may observe
        its namespace under ``storage:read`` alone, while mutations require
        ``storage:write``. Write implies read, so a write-granted mod keeps
        ``get``/``keys``/``snapshot`` on its own namespace.
        """
        self._assert_any_cap("storage:read", "storage:write")
        return self._storage

    @property
    def ui(self) -> UiCapability:
        """Scoped access to UI card rendering."""
        self._assert_cap("ui:render")
        return self._ui

    @property
    def commands(self) -> CommandCapability:
        """Scoped access to mod command registration."""
        self._assert_cap("commands:register")
        return self._commands

    @property
    def fs(self) -> FileCapability:
        """Scoped read-only filesystem access."""
        self._assert_cap("fs:read")
        return self._fs
