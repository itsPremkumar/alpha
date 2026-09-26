"""Parent-side dispatcher for the script bridge.

A tool call made from inside a script arrives here, on the socket, and is routed
through **the same handler and the same middleware decision** a model-issued tool
call would receive:

* the tool list is produced by :func:`alpha.tools.tools.get_available_tools` -
  the identical function the lead agent and the subagent executor call;
* the authorisation decision is produced by the real
  :class:`alpha.guardrails.middleware.GuardrailMiddleware` wrapping the real
  authorisation provider, built by the same construction sequence
  ``tool_error_handling_middleware._build_runtime_middlewares`` uses, and
  evaluated against the *real* run context;
* the bridge's own allowlist is a strict **pre-filter** (deny by default), so
  the script path can only ever be narrower than the model path, never wider.

Nothing about the child is trusted: the child cannot widen its own allowlist,
cannot name a forbidden tool, and cannot reach the cap.  Every one of those is
re-derived here per call.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import (
    DeniedByPolicy,
    PolicyViolation,
    ToolCallCapExceeded,
    ToolNotCallable,
    TransportError,
)
from .policy import ScriptBridgePolicy
from . import wire

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any], "RuntimeCarrier"], Any]


class _RequestStandIn:
    """Duck-typed ``ToolCallRequest`` for the real middleware.

    ``GuardrailMiddleware`` only ever reads ``request.tool_call`` and
    ``request.runtime.context``, so this stand-in lets the *real* middleware
    object run its own request building, provider evaluation and denial-message
    construction unchanged.  Building a genuine graph ``ToolCallRequest`` would
    require a live graph ``Runtime``, which does not exist outside a run.
    """

    __slots__ = ("tool_call", "runtime", "state")

    def __init__(self, tool_call: dict[str, Any], context: dict[str, Any]) -> None:
        self.tool_call = tool_call
        self.runtime = type("R", (), {"context": context, "store": None, "stream_writer": None})()
        self.state: dict[str, Any] = {}


@dataclass
class RuntimeCarrier:
    """Everything parent-side that is derived from the *caller*, not the child."""

    context: dict[str, Any] = field(default_factory=dict)
    thread_id: str = ""
    run_id: str = ""
    user_id: str | None = None
    app_config: Any = None
    tool_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class DispatchStats:
    tool_calls: int = 0
    denied: int = 0
    refused: int = 0
    errors: int = 0
    transcript_bytes: int = 0


class ScriptDispatcher:
    """Owns the listener, the counters and the policy for one execution."""

    def __init__(
        self,
        *,
        policy: ScriptBridgePolicy,
        carrier: RuntimeCarrier | None = None,
        cache_dir: Path,
        on_tool_call: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
        tools: list[Any] | None = None,
    ) -> None:
        self.policy = policy
        self.carrier = carrier or RuntimeCarrier()
        self.cache_dir = cache_dir
        self.stats = DispatchStats()
        self._on_tool_call = on_tool_call
        self._tools_cache: list[Any] | None = list(tools) if tools is not None else None
        self._tools_signature: Any = None
        self._mcp_names: frozenset[str] = frozenset()
        self._call_lock = threading.Lock()
        self._endpoint: wire.Endpoint | None = None
        self._guardrails: list[Any] = []
        self._guardrails_built_for: Any = None
        if self._tools_cache is not None:
            self._mcp_names = frozenset(
                name for name in (getattr(t, "name", "") for t in self._tools_cache) if _looks_like_mcp(name)
            )

    # -- endpoint lifecycle ------------------------------------------------
    def bind(self) -> wire.Endpoint:
        """Bind the loopback listener and remember the endpoint."""
        self._endpoint = wire.make_endpoint(self.policy.cache_dir)
        return self._endpoint

    def close(self) -> None:
        endpoint = self._endpoint
        if endpoint is None:
            return
        listener = getattr(endpoint, "listener", None)
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        if endpoint.kind == "unix":
            try:
                Path(endpoint.address).unlink(missing_ok=True)
            except OSError:
                pass
        self._endpoint = None

    # -- registry ----------------------------------------------------------
    def available_tools(self) -> list[Any]:
        """The live tool list, from the same factory the agent itself uses.

        ``alpha.tools.tools.get_available_tools`` is the exact function
        ``_assemble_lead_agent`` and ``SubagentExecutor`` call, so the bridge
        cannot see a tool the agent could not, nor miss one it could.
        """
        if self._tools_cache is not None:
            return self._tools_cache
        from alpha.tools.tools import get_available_tools

        tools = list(get_available_tools(app_config=self.carrier.app_config))
        self._tools_cache = tools
        self._mcp_names = frozenset(
            name for name in (getattr(t, "name", "") for t in tools) if _looks_like_mcp(name)
        )
        return tools

    # -- policy ------------------------------------------------------------
    def _check_name(self, name: str) -> None:
        if not isinstance(name, str) or not name:
            raise PolicyViolation("tool name must be a non-empty string", tool_name=repr(name))
        allowed, reason = self.policy.is_tool_callable(name, mcp_names=self._mcp_names)
        if not allowed:
            if reason == "not_in_allowlist":
                raise ToolNotCallable(
                    f"tool '{name}' is not in this execution's allowlist",
                    tool_name=name,
                    allowed=list(self.policy.allowed_tool_names),
                )
            raise PolicyViolation(f"tool '{name}' is not callable from a script: {reason}", tool_name=name)

    def _account(self, nbytes: int) -> None:
        self.stats.transcript_bytes += max(0, int(nbytes))
        cap = self.policy.limits.max_transcript_bytes
        if self.stats.transcript_bytes > cap:
            raise ToolCallCapExceeded(
                "script tool-call transcript exceeded its cap; split the work into "
                "smaller scripts and page the result",
                cap_bytes=cap,
                used_bytes=self.stats.transcript_bytes,
            )

    # -- the actual dispatch ----------------------------------------------
    async def dispatch(self, name: str, arguments: dict[str, Any]) -> Any:
        """Route one script-issued tool call through the runtime's own path."""
        self._check_name(name)
        with self._call_lock:
            if self.stats.tool_calls >= self.policy.limits.max_tool_calls:
                # Counted once, at the socket boundary in ``_serve_connection``;
                # counting here too would double-report a single refusal.
                raise ToolCallCapExceeded(
                    "script exceeded its tool-call budget; the dispatcher refused "
                    "further calls rather than truncating the run",
                    cap=self.policy.limits.max_tool_calls,
                    used=self.stats.tool_calls,
                )
            self.stats.tool_calls += 1

        tools = self.available_tools()
        tool = next((t for t in tools if getattr(t, "name", None) == name), None)
        if tool is None:
            # Counted once, at the socket boundary in ``_serve_connection``.
            raise ToolNotCallable(
                f"tool '{name}' is not present in the runtime's assembled tool list",
                tool_name=name,
                available=len(tools),
            )

        call_id = f"script-bridge-{self.stats.tool_calls:04d}"
        stand_in = _RequestStandIn({"name": name, "args": dict(arguments), "id": call_id}, self.carrier.context)

        guardrails = self._ensure_guardrails()
        if not guardrails:
            value = await self._invoke(tool, arguments)
        else:
            value = await _run_through_guardrails(guardrails, stand_in, self._invoke_tool(tool))

        self._account(len(str(value).encode("utf-8", "replace")))
        return value

    def _invoke_tool(self, tool: Any) -> Callable[[_RequestStandIn], Awaitable[Any]]:
        async def _call(request: _RequestStandIn) -> Any:
            return await self._invoke(tool, request.tool_call.get("args") or {})

        return _call

    async def _invoke(self, tool: Any, arguments: dict[str, Any]) -> Any:
        ainvoke = getattr(tool, "ainvoke", None)
        if callable(ainvoke):
            return await ainvoke(arguments)
        result = tool.invoke(arguments)
        if asyncio.iscoroutine(result):
            return await result
        return result

    # -- authorisation -----------------------------------------------------
    def _ensure_guardrails(self) -> list[Any]:
        """Build the same guardrail middleware list the runtime chain builds.

        This mirrors ``tool_error_handling_middleware._build_runtime_middlewares``
        lines 256-274 (authorisation adapter) and 276+ (explicit guardrails).
        It is a *decision*, not a re-implementation: the middleware and the
        provider are the real objects, so a script call and a model call are
        judged by identical code.
        """
        app_config = self.carrier.app_config
        signature = id(app_config)
        if self._guardrails_built_for == signature and self._guardrails:
            return self._guardrails
        self._guardrails = []
        self._guardrails_built_for = signature
        if app_config is None:
            return self._guardrails
        try:
            authorization_config = getattr(app_config, "authorization", None)
            if authorization_config is not None and getattr(authorization_config, "enabled", False) is True:
                from alpha.authz.adapter import GuardrailAuthorizationAdapter
                from alpha.authz.runtime import resolve_authorization_provider
                from alpha.guardrails.middleware import GuardrailMiddleware

                provider = resolve_authorization_provider(authorization_config)
                if provider is not None:
                    self._guardrails.append(
                        GuardrailMiddleware(
                            GuardrailAuthorizationAdapter(
                                provider,
                                default_role=authorization_config.default_role,
                            ),
                            fail_closed=authorization_config.fail_closed,
                        )
                    )
            guardrails_config = getattr(app_config, "guardrails", None)
            if (
                guardrails_config is not None
                and getattr(guardrails_config, "enabled", False)
                and getattr(guardrails_config, "provider", None)
            ):
                import inspect

                from alpha.guardrails.middleware import GuardrailMiddleware
                from alpha.reflection import resolve_variable

                provider_cls = resolve_variable(guardrails_config.provider.use)
                provider_kwargs = dict(guardrails_config.provider.config or {})
                if "framework" not in provider_kwargs:
                    try:
                        sig = inspect.signature(provider_cls.__init__)
                        if "framework" in sig.parameters or any(
                            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                        ):
                            provider_kwargs["framework"] = "alpha"
                    except (ValueError, TypeError):
                        pass
                self._guardrails.append(
                    GuardrailMiddleware(provider_cls(**provider_kwargs))
                )
        except Exception:  # noqa: BLE001 - never let policy construction break dispatch
            logger.exception("script bridge guardrail construction failed; failing closed")
            raise DeniedByPolicy(
                "authorisation could not be evaluated; the script tool call was refused "
                "rather than allowed unevaluated"
            ) from None
        return self._guardrails

    # -- socket server -----------------------------------------------------
    def serve(self, *, deadline: float, stop: threading.Event | None = None) -> str:
        """Serve child connections until *deadline* or until *stop* is set.

        Returns a short status word the caller can surface: ``served`` when at
        least one call was dispatched, ``no_client`` when the script made no
        tool call at all (a legitimate outcome, not an error).

        *stop* is how the owner ends the wait the moment its child exits, so a
        finished script is not held open by an idle accept loop.
        """
        endpoint = self._endpoint
        if endpoint is None:  # pragma: no cover - defensive
            raise TransportError("dispatcher has no bound endpoint")
        listener = wire.listen(endpoint)
        served = 0
        while time.monotonic() < deadline:
            if stop is not None and stop.is_set():
                break
            listener.settimeout(min(0.2, max(0.01, deadline - time.monotonic())))
            try:
                conn, _ = listener.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError as exc:
                raise TransportError(f"dispatcher accept failed: {exc}") from exc
            try:
                conn.settimeout(max(1.0, deadline - time.monotonic()))
                before = self.stats.tool_calls
                self._serve_connection(conn)
                served += self.stats.tool_calls - before
            except TransportError:
                raise
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
        return "served" if served or self.stats.tool_calls else "no_client"

    def serve_once(self, *, accept_timeout: float) -> str:
        """Serve a single child connection.  Returns the same status word."""
        return self.serve(deadline=time.monotonic() + accept_timeout)

    def _serve_connection(self, conn: Any) -> None:
        endpoint = self._endpoint
        assert endpoint is not None
        frames = wire.read_frames(conn)
        try:
            hello = wire.authenticate(frames, endpoint.token)
        except TransportError:
            raise
        for frame in frames:
            op = frame.get("op")
            if op == "bye":
                return
            if op != "tool_call":
                _reply_error(conn, frame, "unknown_op", f"unsupported op {op!r}")
                continue
            name = str(frame.get("name", ""))
            arguments = frame.get("arguments") or {}
            if not isinstance(arguments, dict):
                _reply_error(conn, frame, "bad_arguments", "arguments must be an object")
                continue
            try:
                value = asyncio.run(self.dispatch(name, arguments))
            except DeniedByPolicy as exc:
                self.stats.denied += 1
                _reply_error(conn, frame, exc.code, exc.message, exc.detail)
            except ToolCallCapExceeded as exc:
                self.stats.refused += 1
                _reply_error(conn, frame, exc.code, exc.message, exc.detail)
            except Exception as exc:  # noqa: BLE001 - the child must always get an answer
                self.stats.errors += 1
                logger.warning("script bridge tool call %s failed: %s", name, exc)
                _reply_error(
                    conn,
                    frame,
                    getattr(exc, "code", "tool_call_failed"),
                    str(exc),
                    getattr(exc, "detail", {}),
                )
            else:
                _reply_ok(conn, frame, value, self.stats.tool_calls)
        _ = hello


async def _run_through_guardrails(
    guardrails: list[Any], request: _RequestStandIn, innermost: Callable[[_RequestStandIn], Awaitable[Any]]
) -> Any:
    """Run the real middleware chain, outermost first, exactly as the graph does."""

    async def _call(req: _RequestStandIn) -> Any:
        return await innermost(req)

    handler = _call
    for middleware in reversed(guardrails):
        handler = _wrap(middleware, handler)
    return await handler(request)


def _wrap(middleware: Any, handler: Callable[[_RequestStandIn], Awaitable[Any]]) -> Callable[[_RequestStandIn], Awaitable[Any]]:
    awrap = getattr(middleware, "awrap_tool_call", None)
    if callable(awrap):
        async def _call(request: _RequestStandIn) -> Any:
            return await awrap(request, handler)

        return _call
    wrap = getattr(middleware, "wrap_tool_call", None)
    if callable(wrap):
        async def _call_sync(request: _RequestStandIn) -> Any:
            return wrap(request, handler)

        return _call_sync
    return handler


def _reply_ok(conn: Any, frame: dict[str, Any], value: Any, count: int) -> None:
    wire.send_frame(
        conn,
        {"op": "tool_result", "id": frame.get("id"), "value": value, "calls_made": count},
    )


def _reply_error(
    conn: Any, frame: dict[str, Any], code: str, message: str, detail: dict[str, Any] | None = None
) -> None:
    wire.send_frame(
        conn,
        {
            "op": "tool_error",
            "id": frame.get("id"),
            "error": {"code": code, "message": message, "detail": detail or {}},
        },
    )


def _looks_like_mcp(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(("mcp__", "mcp_", "__mcp", "alpha_mcp"))


class _SocketsClosed(Exception):  # pragma: no cover - reserved
    pass


def now() -> float:
    return time.monotonic()
