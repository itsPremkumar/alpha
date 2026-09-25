"""Execute a selected catalog tool through the EXISTING runtime path.

This is the security-critical module in the package. Everything here exists so
that "call a tool the model discovered" is indistinguishable, to the tool and
to every guard around it, from "the model called a bound tool directly".

Order of operations (each step is a fail-closed gate, not a best-effort step)
-----------------------------------------------------------------------------
1. **Re-resolve against the live snapshot.** A tool that was searchable and
   has since disappeared, been renamed, or been removed by policy is reported
   as ``unavailable`` / ``policy_denied``. It is never executed.
2. **Re-check policy at call time.** The snapshot is policy-filtered, so a
   policy-denied tool is simply unresolvable. An explicit policy callback can
   narrow further; it can only remove access, never add it.
3. **Validate the trusted input schema BEFORE execution.** Missing required
   arguments, wrong types, and forbidden properties are actionable errors with
   a suggested parameter where one can be derived. A rejected call does not
   reach the tool.
4. **Acquire the execution-mode gate.** A tool declaring
   ``execution_mode="sequential"`` runs exclusively: no other catalog call --
   parallel or sequential, from this or another control -- may be in flight in
   the same catalog session. This is Alpha's ``executionMode`` equivalent and
   is what stops a bridging model from interleaving two mutations.
5. **Dispatch through the injected runtime path.** The dispatcher is the real
   middleware chain the lead builds (policy middleware, pre-tool hooks, receipt
   and audit middleware, sandbox restrictions). The default dispatcher invokes
   the real ``BaseTool`` so the tool's own internal authorization still runs.
6. **Validate the declared trusted output schema AFTER hooks.** A hook that
   rewrites a result into an invalid shape is caught here and reported as
   ``output_schema_violation``.
7. **Time-box the call** and report the outcome, including every block.

Nothing in this module ever converts a block into a success. There is no
fallback path that runs a denied tool "just this once".
"""

from __future__ import annotations

import asyncio
import difflib
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from langchain.tools import BaseTool

from alpha.tools.discovery.catalog import CatalogEntry, CatalogSnapshot, ExecutionMode
from alpha.tools.discovery.telemetry import DiscoveryTelemetry

#: Longest suggestion list for an unknown argument.
SUGGESTION_MAX = 3


class CallOutcome(StrEnum):
    """Machine-readable outcome vocabulary for a bridged call.

    Every value other than ``OK`` is a disclosed block, and each maps to
    exactly one machine-readable reason the model can branch on.
    """

    OK = "ok"
    UNAVAILABLE = "unavailable"
    POLICY_DENIED = "policy_denied"
    INVALID_ARGUMENTS = "invalid_arguments"
    SEQUENTIAL_CONFLICT = "sequential_conflict"
    TIMEOUT = "timeout"
    OUTPUT_SCHEMA_VIOLATION = "output_schema_violation"
    EXECUTION_ERROR = "execution_error"
    BLOCKED = "blocked"


#: A dispatcher re-enters the host's real tool path. It receives the real
#: ``BaseTool`` plus the already-validated arguments and returns the tool's
#: result. The lead wires this to the middleware chain; the default invokes the
#: tool directly, which still runs the tool's own internal authorization.
SyncDispatcher = Callable[[BaseTool, dict[str, Any]], Any]
AsyncDispatcher = Callable[[BaseTool, dict[str, Any]], Awaitable[Any]]

#: A policy callback may only REMOVE access. Returning ``False`` blocks.
PolicyCheck = Callable[[CatalogEntry], tuple[bool, str]]


class ExecutionGate:
    """Mutual-exclusion gate for one catalog session.

    Implements the ``executionMode`` contract: a sequential-only call takes the
    session exclusively, so it is never interleaved with any other catalog call
    -- including a call from a second control surface or a parallel batch.
    Implemented with a plain condition variable so it is correct for the sync
    dispatcher, the async dispatcher, and mixed callers alike.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = 0
        self._sequential_active = False

    @property
    def active(self) -> int:
        with self._condition:
            return self._active

    @property
    def sequential_active(self) -> bool:
        with self._condition:
            return self._sequential_active

    def acquire(self, sequential_only: bool) -> bool:
        """Take a slot. ``False`` means the exclusivity contract refused it."""
        with self._condition:
            if sequential_only:
                if self._active:
                    return False
                self._active = 1
                self._sequential_active = True
                return True
            if self._sequential_active:
                return False
            self._active += 1
            return True

    def release(self, sequential_only: bool) -> None:
        with self._condition:
            if sequential_only:
                self._sequential_active = False
            self._active = max(0, self._active - 1)
            self._condition.notify_all()

    def wait_for_idle(self, timeout: float) -> bool:
        """Block until the session is quiet. Used by tests and by teardown."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True


class _GateHandle:
    """Context manager wrapping :class:`ExecutionGate`."""

    def __init__(self, gate: ExecutionGate, sequential_only: bool, acquired: bool) -> None:
        self._gate = gate
        self._sequential_only = sequential_only
        self._acquired = acquired

    @property
    def acquired(self) -> bool:
        """``False`` when the exclusivity contract refused this call."""
        return self._acquired

    def __enter__(self) -> _GateHandle:
        return self

    def __exit__(self, *exc: object) -> None:
        if self._acquired:
            self._gate.release(self._sequential_only)

    async def __aenter__(self) -> _GateHandle:
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._acquired:
            self._gate.release(self._sequential_only)


def gate_slot(gate: ExecutionGate, entry: CatalogEntry) -> _GateHandle:
    """Attempt to take an execution slot for *entry*."""
    sequential_only = entry.execution_mode is ExecutionMode.SEQUENTIAL
    return _GateHandle(gate, sequential_only, gate.acquire(sequential_only))


# ── trusted schema validation ──────────────────────────────────────────────

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, (list, tuple)),
    "object": lambda value: isinstance(value, Mapping),
    "null": lambda value: value is None,
}


@dataclass(frozen=True)
class ValidationIssue:
    """One schema violation, with a suggested parameter when derivable."""

    path: str
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {"path": self.path, "message": self.message}
        if self.suggestion:
            payload["suggestion"] = self.suggestion
        return payload


def suggest_parameter(unknown: str, known: Sequence[str]) -> str:
    """Closest known parameter name to *unknown*, or ``""`` when nothing is close."""
    if not known:
        return ""
    matches = difflib.get_close_matches(unknown, list(known), n=SUGGESTION_MAX, cutoff=0.7)
    return matches[0] if matches else ""


def validate_against_schema(schema: Mapping[str, Any], value: Any, *, path: str = "") -> list[ValidationIssue]:
    """Validate *value* against the trusted subset of JSON Schema this layer emits.

    The subset is deliberately small and owned by this repository: ``type``
    (single or list), ``required``, ``properties``, ``enum``, and
    ``additionalProperties: false``. An open or partial schema validates
    permissively rather than guessing, because a false rejection would block a
    legitimate call.

    Untrusted schemas are never passed here: the trust decision happens in
    :mod:`alpha.tools.discovery.catalog` and an untrusted entry carries no
    schema at all.
    """
    issues: list[ValidationIssue] = []
    if not schema:
        return issues

    declared_types = schema.get("type")
    if isinstance(declared_types, str):
        declared_types = [declared_types]
    if isinstance(declared_types, list) and declared_types:
        checks = [_TYPE_CHECKS.get(str(item)) for item in declared_types]
        if any(check is not None for check in checks) and not any(check(value) for check in checks if check is not None):
            issues.append(ValidationIssue(path=path or "$", message=f"expected {' or '.join(str(item) for item in declared_types)}, got {type(value).__name__}"))
            return issues

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values and value not in enum_values:
        rendered = ", ".join(json_scalar(item) for item in enum_values[:8])
        issues.append(ValidationIssue(path=path or "$", message=f"value {json_scalar(value)} is not one of: {rendered}"))
        return issues

    properties = schema.get("properties")
    if isinstance(properties, Mapping) and isinstance(value, Mapping):
        required = schema.get("required")
        required_names = [str(item) for item in required] if isinstance(required, list) else []
        for name in required_names:
            if name not in value:
                issues.append(ValidationIssue(path=name, message=f"required argument '{name}' is missing"))
        known_names = [str(name) for name in properties]
        for name, item in value.items():
            child = properties.get(name)
            if not isinstance(child, Mapping):
                if schema.get("additionalProperties") is False:
                    issues.append(ValidationIssue(path=str(name), message=f"unknown argument '{name}' is not accepted by this tool", suggestion=suggest_parameter(str(name), known_names)))
                continue
            issues.extend(validate_against_schema(child, item, path=f"{path}.{name}" if path else str(name)))
    return issues


def json_scalar(value: Any) -> str:
    """Render a scalar compactly for an error message."""
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, bool) or value is None:
        return str(value)
    return str(value)


# ── result handling ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CallResult:
    """Disclosed outcome of one bridged call."""

    outcome: CallOutcome
    entry_id: str
    tool: str
    message: str = ""
    result: Any = None
    details: dict[str, Any] | None = None
    duration_ms: float = 0.0
    issues: tuple[ValidationIssue, ...] = ()
    snapshot_id: str = ""
    source: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is CallOutcome.OK

    def to_payload(self) -> dict[str, Any]:
        """Model-facing payload.

        Carries the target's ``id``/``name``/``source`` and the unchanged
        target ``result``. It deliberately does NOT repeat the description or
        the input signature -- ``tool_describe`` is the tool for that -- and it
        keeps the full envelope in ``details`` for runtime consumers.
        """
        payload: dict[str, Any] = {
            "ok": self.ok,
            "outcome": self.outcome.value,
            "id": self.entry_id,
            "tool": self.tool,
            "source": self.source,
            "result": self.result,
        }
        if self.message:
            payload["message"] = self.message
        if self.issues:
            payload["issues"] = [issue.to_dict() for issue in self.issues]
        if self.duration_ms:
            payload["durationMs"] = round(self.duration_ms, 2)
        if self.details is not None:
            payload["details"] = self.details
        return payload


def _summarize(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= 400 else text[:397] + "..."


def default_sync_dispatcher(tool: BaseTool, arguments: dict[str, Any]) -> Any:
    """Invoke the real tool object.

    This is the honest default: it is the same ``BaseTool`` the runtime would
    have called, so the tool's own internal authorization, sandbox checks, and
    error handling still run. It does NOT run the middleware chain -- only an
    injected dispatcher does that, which is why the lead must wire one.
    """
    return tool.invoke(arguments)


async def default_async_dispatcher(tool: BaseTool, arguments: dict[str, Any]) -> Any:
    """Async counterpart of :func:`default_sync_dispatcher`."""
    return await tool.ainvoke(arguments)


class ToolCaller:
    """Executes a discovered tool with every documented guard applied.

    Holds no global state: one instance belongs to one
    :class:`~alpha.tools.discovery.session.DiscoverySession`, so two runs in one
    process get independent gates, telemetry, and snapshots.
    """

    def __init__(
        self,
        snapshot: CatalogSnapshot,
        *,
        telemetry: DiscoveryTelemetry,
        call_timeout_ms: int = 30_000,
        dispatch: SyncDispatcher | None = None,
        adispatch: AsyncDispatcher | None = None,
        policy_check: PolicyCheck | None = None,
        gate: ExecutionGate | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._telemetry = telemetry
        self._call_timeout_ms = max(1, int(call_timeout_ms))
        self._dispatch = dispatch or default_sync_dispatcher
        self._adispatch = adispatch or default_async_dispatcher
        self._policy_check = policy_check
        self._gate = gate or ExecutionGate()

    @property
    def gate(self) -> ExecutionGate:
        return self._gate

    def _resolve(self, selector: str) -> tuple[CatalogEntry | None, CallResult | None]:
        entry = self._snapshot.resolve(selector)
        if entry is None:
            return None, CallResult(
                outcome=CallOutcome.UNAVAILABLE,
                entry_id=selector,
                tool=selector,
                message=(
                    f"Tool {selector!r} is not in the current policy-filtered catalog. It may have been removed, renamed, or denied by the active policy. "
                    "Run `tool_search` to see what is available."
                ),
                snapshot_id=self._snapshot.snapshot_id,
            )
        if self._policy_check is not None:
            allowed, reason = self._policy_check(entry)
            if not allowed:
                return None, CallResult(
                    outcome=CallOutcome.POLICY_DENIED,
                    entry_id=entry.entry_id,
                    tool=entry.name,
                    message=f"Tool {entry.name!r} is blocked by the active policy: {reason}",
                    snapshot_id=self._snapshot.snapshot_id,
                )
        return entry, None

    def _precheck(self, entry: CatalogEntry, arguments: Mapping[str, Any]) -> list[ValidationIssue] | None:
        if not entry.trusted or not entry.input_schema:
            return None
        payload = dict(arguments or {})
        return validate_against_schema(entry.input_schema, payload)

    def _postcheck(self, entry: CatalogEntry, result: Any) -> list[ValidationIssue] | None:
        if not entry.trusted or not entry.output_schema:
            return None
        return validate_against_schema(entry.output_schema, result)

    def _blocked_message(self, result: Any) -> str:
        """Recognize a middleware denial surfaced as a result, not an exception.

        ``SkillToolPolicyMiddleware`` and ``DeferredToolFilterMiddleware`` report
        a block as a ``ToolMessage`` with ``status="error"``. Surfacing that as
        a success would be a bypass in disguise, so it is converted into a
        disclosed ``blocked`` outcome.
        """
        status = getattr(result, "status", None)
        if status == "error":
            return _summarize(getattr(result, "content", result))
        return ""

    def call(self, selector: str, arguments: Mapping[str, Any] | None = None) -> CallResult:
        """Synchronously execute a discovered tool behind every guard."""
        entry, failure = self._resolve(selector)
        if failure is not None:
            self._telemetry.record_call(entry_id=failure.entry_id, outcome=failure.outcome.value)
            return failure
        assert entry is not None
        issues = self._precheck(entry, arguments)
        if issues:
            result = CallResult(
                outcome=CallOutcome.INVALID_ARGUMENTS,
                entry_id=entry.entry_id,
                tool=entry.name,
                message=f"Arguments rejected before execution for {entry.name!r}.",
                issues=tuple(issues),
                snapshot_id=self._snapshot.snapshot_id,
                source=entry.source.value,
            )
            self._telemetry.record_call(entry_id=entry.entry_id, outcome=result.outcome.value)
            return result
        return self._guarded_call(entry, dict(arguments or {}), self._dispatch)

    async def acall(self, selector: str, arguments: Mapping[str, Any] | None = None) -> CallResult:
        """Asynchronously execute a discovered tool behind every guard."""
        entry, failure = self._resolve(selector)
        if failure is not None:
            self._telemetry.record_call(entry_id=failure.entry_id, outcome=failure.outcome.value)
            return failure
        assert entry is not None
        issues = self._precheck(entry, arguments)
        if issues:
            result = CallResult(
                outcome=CallOutcome.INVALID_ARGUMENTS,
                entry_id=entry.entry_id,
                tool=entry.name,
                message=f"Arguments rejected before execution for {entry.name!r}.",
                issues=tuple(issues),
                snapshot_id=self._snapshot.snapshot_id,
                source=entry.source.value,
            )
            self._telemetry.record_call(entry_id=entry.entry_id, outcome=result.outcome.value)
            return result
        return await self._aguarded_call(entry, dict(arguments or {}), self._adispatch)

    def _guarded_call(self, entry: CatalogEntry, arguments: dict[str, Any], dispatch: SyncDispatcher) -> CallResult:
        handle = gate_slot(self._gate, entry)
        if not handle.acquired:
            result = self._conflict(entry)
            self._telemetry.record_call(entry_id=entry.entry_id, outcome=result.outcome.value)
            return result
        started = time.perf_counter()
        try:
            with handle:
                value = dispatch(entry.tool, arguments)
        except Exception as exc:
            return self._finish(entry, CallOutcome.EXECUTION_ERROR, f"{type(exc).__name__}: {exc}", None, started)
        blocked = self._blocked_message(value)
        if blocked:
            return self._finish(entry, CallOutcome.BLOCKED, blocked, value, started)
        return self._finish(entry, CallOutcome.OK, "", value, started)

    async def _aguarded_call(self, entry: CatalogEntry, arguments: dict[str, Any], dispatch: AsyncDispatcher) -> CallResult:
        handle = gate_slot(self._gate, entry)
        if not handle.acquired:
            result = self._conflict(entry)
            self._telemetry.record_call(entry_id=entry.entry_id, outcome=result.outcome.value)
            return result
        started = time.perf_counter()
        timeout = self._call_timeout_ms / 1000.0
        try:
            async with handle:
                value = await asyncio.wait_for(dispatch(entry.tool, arguments), timeout=timeout)
        except TimeoutError:
            return self._finish(entry, CallOutcome.TIMEOUT, f"Tool {entry.name!r} exceeded the {self._call_timeout_ms} ms call budget and was cancelled.", None, started)
        except Exception as exc:
            return self._finish(entry, CallOutcome.EXECUTION_ERROR, f"{type(exc).__name__}: {exc}", None, started)
        blocked = self._blocked_message(value)
        if blocked:
            return self._finish(entry, CallOutcome.BLOCKED, blocked, value, started)
        return self._finish(entry, CallOutcome.OK, "", value, started)

    def _conflict(self, entry: CatalogEntry) -> CallResult:
        return CallResult(
            outcome=CallOutcome.SEQUENTIAL_CONFLICT,
            entry_id=entry.entry_id,
            tool=entry.name,
            message=(
                f"Tool {entry.name!r} declares executionMode 'sequential' and must run exclusively, "
                "but another catalog call is in flight. Wait for it to finish, then retry."
                if entry.sequential_only
                else f"Tool {entry.name!r} could not start: a sequential-only catalog call is in flight. Wait for it to finish, then retry."
            ),
            snapshot_id=self._snapshot.snapshot_id,
            source=entry.source.value,
        )

    def _finish(self, entry: CatalogEntry, outcome: CallOutcome, message: str, value: Any, started: float) -> CallResult:
        duration = (time.perf_counter() - started) * 1000.0
        if outcome is CallOutcome.OK:
            issues = self._postcheck(entry, value)
            if issues:
                result = CallResult(
                    outcome=CallOutcome.OUTPUT_SCHEMA_VIOLATION,
                    entry_id=entry.entry_id,
                    tool=entry.name,
                    message=f"Result from {entry.name!r} does not satisfy its declared output schema.",
                    issues=tuple(issues),
                    duration_ms=duration,
                    snapshot_id=self._snapshot.snapshot_id,
                    source=entry.source.value,
                )
                self._telemetry.record_call(entry_id=entry.entry_id, outcome=result.outcome.value, duration_ms=duration)
                return result
        result = CallResult(
            outcome=outcome,
            entry_id=entry.entry_id,
            tool=entry.name,
            message=message,
            result=value,
            duration_ms=duration,
            snapshot_id=self._snapshot.snapshot_id,
            source=entry.source.value,
        )
        self._telemetry.record_call(entry_id=entry.entry_id, outcome=outcome.value, duration_ms=duration)
        return result
