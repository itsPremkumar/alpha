"""The script-bridge tool surface.

This module is the runtime entry point.  It is registered into the shared
``UniversalToolCatalog``, so the already-registered ``catalog_tool_call`` tool
reaches it - a real runtime path, not a test-only import.

Two modes, one policy:

``oneshot``  a fresh child per call.  Nothing survives.
``kernel``   a persistent child per ``session_id``; imports and variables
             survive between cells.  The environment is **frozen at spawn**.

Changing mode changes WHERE the script runs.  It never changes what the script
can see or reach: same allowlist, same limits, same authorisation chain, same
environment policy.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dispatcher import RuntimeCarrier, ScriptDispatcher
from .env import build_child_env
from .errors import (
    OutputCapExceeded,
    PolicyViolation,
    ScriptBridgeError,
    ToolCallCapExceeded,
    TransportError,
    WallClockTimeout,
)
from .kernel import KernelManager
from .policy import (
    ScriptBridgeLimits,
    ScriptBridgeMode,
    ScriptBridgePolicy,
    forbidden_tool_names_sorted,
)
from .runner import run_script
from . import stubgen, wire

logger = logging.getLogger(__name__)

TOOL_NAME = "script_bridge"
RESET_TOOL_NAME = "script_bridge_reset"
STUB_MODULE = "alpha.script_bridge_child"


def _cache_root() -> Path:
    from alpha.config.paths import get_paths

    base = get_paths().base_dir
    return Path(base) / "script-bridge"


@dataclass
class BridgeResult:
    payload: dict[str, Any]

    def render(self) -> str:
        return json.dumps(self.payload, indent=2, ensure_ascii=False, default=str)


class ScriptBridgeService:
    """One instance per (thread, run) pair; owns kernels and the cache dir."""

    def __init__(
        self,
        carrier: RuntimeCarrier,
        cache_root: Path | None = None,
        *,
        tools: list[Any] | None = None,
    ) -> None:
        self.carrier = carrier
        self.cache_root = Path(cache_root) if cache_root else _cache_root()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        limits = ScriptBridgeLimits()
        self.limits = limits
        self.kernels = KernelManager(limits, self.cache_root)
        self._lock = threading.RLock()
        self._tools = tools

    # -- policy ------------------------------------------------------------
    def build_policy(
        self,
        *,
        allowed_tool_names: list[str] | None,
        mode: str,
        cwd: str | None,
        limits: ScriptBridgeLimits | None,
        env_opt_in: dict[str, str] | None,
    ) -> ScriptBridgePolicy:
        allow = tuple(dict.fromkeys(allowed_tool_names or ()))
        self._reject_forbidden(allow)
        return ScriptBridgePolicy(
            limits=limits or self.limits,
            mode=ScriptBridgeMode(name=mode, cwd=cwd),
            allowed_tool_names=allow,
            env_opt_in=dict(env_opt_in or {}),
            cache_dir=str(self.cache_root),
        )

    @staticmethod
    def _reject_forbidden(allow: tuple[str, ...]) -> None:
        forbidden = set(forbidden_tool_names_sorted())
        clash = sorted(set(allow) & forbidden)
        if clash:
            raise PolicyViolation(
                "these tools can never be called from inside a script "
                "(self-recursion, delegation and approval surfaces): "
                + ", ".join(clash),
                refused=clash,
            )

    # -- one-shot ----------------------------------------------------------
    def run_oneshot(
        self,
        script: str,
        policy: ScriptBridgePolicy,
        *,
        session_id: str = "oneshot",
    ) -> BridgeResult:
        """Run one script in a fresh child and return only what it printed."""
        if len(script.encode("utf-8")) > policy.limits.max_script_bytes:
            raise PolicyViolation(
                "script exceeds the size ceiling",
                max_script_bytes=policy.limits.max_script_bytes,
                supplied_bytes=len(script.encode("utf-8")),
            )
        dispatcher = ScriptDispatcher(
            policy=policy, carrier=self.carrier, cache_dir=self.cache_root, tools=self._tools
        )
        endpoint = dispatcher.bind()
        env = build_child_env(
            opt_in=policy.env_opt_in or None,
            extra={
                **endpoint.to_env(),
                "ALPHA_SCRIPT_BRIDGE_TRANSPORT": endpoint.kind,
                "ALPHA_SCRIPT_BRIDGE_STUB_SHA": _stub_fingerprint(),
                "ALPHA_SCRIPT_BRIDGE_MODE": policy.mode.name,
                "ALPHA_SCRIPT_BRIDGE_KERNEL": "0",
            },
        )
        served = "no_client"
        thread: threading.Thread | None = None
        stop = threading.Event()
        try:
            dispatcher.available_tools()
            budget = policy.limits.wall_clock_seconds
            deadline = time.monotonic() + budget + policy.limits.sigterm_grace_seconds + 5.0

            def _serve() -> None:
                nonlocal served
                try:
                    served = dispatcher.serve(deadline=deadline, stop=stop)
                except Exception:  # noqa: BLE001 - surfaced below via stats
                    logger.debug("script bridge dispatcher stopped early", exc_info=True)

            thread = threading.Thread(target=_serve, daemon=True, name="sb-dispatcher")
            thread.start()
            outcome = run_script(
                script,
                env=env,
                mode=policy.mode,
                limits=policy.limits,
                cache_dir=self.cache_root,
                session_id=session_id,
            )
        finally:
            # Release the accept loop the instant the child is gone, so a
            # finished script is not held open for the rest of its budget.
            stop.set()
            if thread is not None:
                thread.join(timeout=10.0)
            dispatcher.close()
        return self._render_outcome(outcome, dispatcher, policy, served=served, mode="oneshot")

    # -- kernel ------------------------------------------------------------
    def run_kernel(
        self,
        script: str,
        policy: ScriptBridgePolicy,
        *,
        session_id: str,
        owner: str = "top",
    ) -> BridgeResult:
        """Run one cell in a persistent kernel, or degrade to one-shot loudly."""
        degradations: list[str] = []
        session = self.kernels.get(session_id)
        dispatcher: ScriptDispatcher | None = None
        if session is None or not session.alive():
            dispatcher = ScriptDispatcher(
                policy=policy, carrier=self.carrier, cache_dir=self.cache_root, tools=self._tools
            )
            endpoint = dispatcher.bind()
            thread: threading.Thread | None = None

            def _factory(sess: Any) -> tuple[ScriptDispatcher, threading.Thread]:
                nonlocal thread
                budget = policy.limits.wall_clock_seconds
                deadline = time.monotonic() + 24 * 3600

                def _serve() -> None:
                    try:
                        dispatcher.serve(deadline=deadline)  # type: ignore[union-attr]
                    except Exception:  # noqa: BLE001 - kernel teardown races
                        logger.debug("kernel dispatcher stopped", exc_info=True)

                th = threading.Thread(target=_serve, daemon=True, name=f"sb-kernel-dispatch-{session_id}")
                th.start()
                thread = th
                return dispatcher, th  # type: ignore[return-value]

            try:
                session = self.kernels.spawn(
                    session_id,
                    owner=owner,
                    env_opt_in=policy.env_opt_in or None,
                    cwd=policy.mode.cwd,
                    dispatcher_factory=_factory,
                    env_extra={
                        **endpoint.to_env(),
                        "ALPHA_SCRIPT_BRIDGE_TRANSPORT": endpoint.kind,
                        "ALPHA_SCRIPT_BRIDGE_STUB_SHA": _stub_fingerprint(),
                        "ALPHA_SCRIPT_BRIDGE_MODE": policy.mode.name,
                        "ALPHA_SCRIPT_BRIDGE_KERNEL": "1",
                    },
                )
                session.dispatcher = dispatcher
            except Exception as exc:
                dispatcher.close()
                code = getattr(exc, "code", "kernel_unavailable")
                degradations.append(
                    f"{code}: {exc}. FELL BACK TO ONE-SHOT EXECUTION for this call; "
                    "nothing persists between cells in this mode."
                )
                return self.run_oneshot(
                    script, policy, session_id=f"{session_id}-oneshot"
                )
        else:
            dispatcher = session.dispatcher
            thread = getattr(session, "server", None)

        dispatcher = session.dispatcher
        if dispatcher is not None:
            dispatcher.policy = policy
        try:
            cell = self.kernels.run_cell(session, script)
        except WallClockTimeout as exc:
            self.kernels.reset(session_id)
            return BridgeResult(
                {
                    "status": "error",
                    "error": exc.to_dict(),
                    "mode": "kernel",
                    "kernel": session.describe(),
                    "degradations": degradations,
                }
            )
        except ScriptBridgeError as exc:
            self.kernels.reset(session_id)
            return BridgeResult(
                {
                    "status": "error",
                    "error": exc.to_dict(),
                    "mode": "kernel",
                    "kernel": session.describe(),
                    "degradations": degradations,
                }
            )
        payload: dict[str, Any] = {
            "status": "ok" if cell.ok else "error",
            "mode": "kernel",
            "session_id": session_id,
            "kernel": session.describe(),
            "tool_calls_this_cell": cell.tool_calls,
            "stdout": cell.stdout,
            "stderr": cell.stderr,
            "degradations": degradations,
        }
        if cell.error:
            payload["error"] = cell.error
        if dispatcher is not None:
            payload["stats"] = _stats_dict(dispatcher)
        return BridgeResult(payload)

    # -- rendering ---------------------------------------------------------
    def _render_outcome(
        self,
        outcome: Any,
        dispatcher: ScriptDispatcher,
        policy: ScriptBridgePolicy,
        *,
        served: str,
        mode: str,
    ) -> BridgeResult:
        stats = _stats_dict(dispatcher)
        payload: dict[str, Any] = {
            "status": "ok",
            "mode": mode,
            "returncode": outcome.returncode,
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
            "duration_seconds": round(outcome.duration_seconds, 4),
            "tool_calls": stats["tool_calls"],
            "denied": stats["denied"],
            "refused": stats["refused"],
            "errors": stats["errors"],
            "dispatcher": served,
            "limits": policy.limits.to_dict(),
            "transport": wire.transport_kind(),
            "stub_module": STUB_MODULE,
        }
        if outcome.stdout_truncated:
            payload["stdout_truncated"] = True
            payload["stdout_full_text_path"] = outcome.stdout_spill
        if outcome.stderr_truncated:
            payload["stderr_truncated"] = True
            payload["stderr_full_text_path"] = outcome.stderr_spill
        if outcome.timed_out:
            payload["status"] = "error"
            payload["error"] = {
                "code": "wall_clock_timeout",
                "message": (
                    "the script exceeded its wall-clock budget and was terminated "
                    f"(stage: {outcome.killed_by or 'terminate'}). Raise the budget "
                    "explicitly or split the work."
                ),
                "detail": {
                    "wall_clock_seconds": policy.limits.wall_clock_seconds,
                    "sigterm_grace_seconds": policy.limits.sigterm_grace_seconds,
                    "killed_by": outcome.killed_by,
                },
            }
        if stats["refused"]:
            payload["status"] = "error"
            payload["error"] = {
                "code": "tool_call_cap_exceeded",
                "message": (
                    "the script hit its tool-call budget; the dispatcher refused the "
                    "next call rather than silently dropping it"
                ),
                "detail": {"cap": policy.limits.max_tool_calls, "used": stats["tool_calls"]},
            }
        return BridgeResult(payload)

    def reset(self, session_id: str | None) -> list[str]:
        return self.kernels.reset(session_id)

    def describe_sessions(self) -> list[dict[str, Any]]:
        return self.kernels.sessions()


def _stats_dict(dispatcher: ScriptDispatcher) -> dict[str, int]:
    return {
        "tool_calls": dispatcher.stats.tool_calls,
        "denied": dispatcher.stats.denied,
        "refused": dispatcher.stats.refused,
        "errors": dispatcher.stats.errors,
    }


def _stub_fingerprint() -> str:
    """The fingerprint of the generated stub the child is allowed to load."""
    from alpha.script_bridge_child import generated as child_generated

    return child_generated.STUB_REGISTRY_SHA256


# -- the tool entry points ------------------------------------------------
def _carrier_from_context(context: dict[str, Any] | None) -> RuntimeCarrier:
    context = dict(context or {})
    return RuntimeCarrier(
        context=context,
        thread_id=str(context.get("thread_id") or ""),
        run_id=str(context.get("run_id") or ""),
        user_id=context.get("user_id"),
        app_config=context.get("app_config"),
    )


def script_bridge(
    code: str,
    allowed_tools: list[str] | None = None,
    mode: str = "oneshot",
    session_id: str = "default",
    timeout_seconds: float | None = None,
    max_tool_calls: int | None = None,
    env_opt_in: dict[str, str] | None = None,
    persist: bool | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    """Run a Python script that calls Alpha tools, returning only what it prints.

    The script imports the generated stub and calls tools by name; the
    intermediate tool results stay on the parent side and never enter the
    context window.

    Args:
        code: The Python source to run.
        allowed_tools: Exact tool names the script may call.  Empty means none.
        mode: ``oneshot`` (fresh child) or ``kernel`` (persistent child).
        session_id: Kernel identity; state persists per session id in kernel mode.
        timeout_seconds: Wall-clock budget for this execution.
        max_tool_calls: Tool-call budget for this execution.
        env_opt_in: Exact-name environment opt-ins.  Prefixes are never accepted.
        persist: Deprecated alias for ``mode == "kernel"``.
        context: The run context the runtime already resolved.
    """
    effective_mode = mode
    if persist is True and mode == "oneshot":
        effective_mode = "kernel"
    overrides: dict[str, Any] = {}
    if timeout_seconds is not None:
        overrides["wall_clock_seconds"] = float(timeout_seconds)
    if max_tool_calls is not None:
        overrides["max_tool_calls"] = int(max_tool_calls)

    drift = stubgen.check_stub_drift()
    if drift is not None:
        return json.dumps(
            {"status": "error", "error": {"code": "stale_tool_stub", "message": drift}},
            indent=2,
        )

    service = ScriptBridgeService(_carrier_from_context(context))
    try:
        policy = service.build_policy(
            allowed_tool_names=allowed_tools,
            mode="project" if effective_mode == "oneshot" else "project",
            cwd=None,
            limits=ScriptBridgeLimits(**{**ScriptBridgeLimits().to_dict(), **overrides})
            if overrides
            else None,
            env_opt_in=env_opt_in,
        )
    except ScriptBridgeError as exc:
        return json.dumps({"status": "error", "error": exc.to_dict()}, indent=2)

    try:
        if effective_mode == "kernel":
            result = service.run_kernel(code, policy, session_id=session_id)
        else:
            result = service.run_oneshot(code, policy, session_id=session_id)
    except ScriptBridgeError as exc:
        return json.dumps({"status": "error", "error": exc.to_dict()}, indent=2)
    except TransportError as exc:
        return json.dumps(
            {"status": "error", "error": {"code": exc.code, "message": exc.message}}, indent=2
        )
    return result.render()


def script_bridge_reset(session_id: str | None = None, context: dict[str, Any] | None = None) -> str:
    """Drop persistent script-bridge kernels.

    Args:
        session_id: Reset one kernel, or every kernel in this session when omitted.
        context: The run context the runtime already resolved.
    """
    service = ScriptBridgeService(_carrier_from_context(context))
    dropped = service.reset(session_id)
    return json.dumps(
        {"status": "ok", "reset": dropped, "remaining": service.describe_sessions()}, indent=2
    )


SCRIPT_BRIDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "description": "Python source to execute."},
        "allowed_tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Exact tool names the script may call. Empty means none.",
        },
        "mode": {"type": "string", "enum": ["oneshot", "kernel"]},
        "session_id": {"type": "string"},
        "timeout_seconds": {"type": "number"},
        "max_tool_calls": {"type": "integer"},
        "env_opt_in": {"type": "object", "additionalProperties": {"type": "string"}},
        "persist": {"type": "boolean"},
        "context": {"type": "object"},
    },
    "required": ["code"],
}

SCRIPT_BRIDGE_RESET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string", "nullable": True},
        "context": {"type": "object"},
    },
}


def register_in_catalog(catalog: Any = None) -> bool:
    """Register the bridge into the shared :class:`UniversalToolCatalog`.

    ``catalog_tool_call`` is already a registered, manifest-pinned tool, so this
    is the wiring point that makes the bridge reachable from a real run without
    touching the generated tool registry.
    """
    if catalog is None:
        from alpha.tools.search.catalog import get_universal_catalog

        catalog = get_universal_catalog()
    catalog.register_tool(
        TOOL_NAME,
        script_bridge,
        description=(
            "Run a Python script that calls Alpha tools programmatically and return "
            "only the script's own output. Intermediate tool results never enter the "
            "context window."
        ),
        category="code",
        parameters_schema=SCRIPT_BRIDGE_SCHEMA,
    )
    catalog.register_tool(
        RESET_TOOL_NAME,
        script_bridge_reset,
        description="Drop persistent script-bridge kernels for this session.",
        category="code",
        parameters_schema=SCRIPT_BRIDGE_RESET_SCHEMA,
    )
    return True


def _unused_os_guard() -> None:  # pragma: no cover - keeps the import honest
    _ = os.name
