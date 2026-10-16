"""Trajectory flight-recorder middleware.

The trajectory store (``alpha.trajectory.store.TrajectoryStore``) is the
agent's durable execution recorder: every completed turn is appended as a
``StepRecord`` so a goal's full thought/tool/output trajectory can be
replayed, forked, or audited after the fact. The Gateway lifespan wires a
store onto ``app.state.trajectory_store``; this middleware is the writer
that actually records steps during a run.

Recording model:

* One :class:`~alpha.trajectory.models.StepRecord` per agent invocation, at
  ``after_agent`` (and its async twin ``aafter_agent``) — the natural
  turn boundary, so a single record captures one full turn's outcome.
* ``goal_id`` is the run's ``thread_id`` (from ``runtime.context``). A thread
  *is* the goal a trajectory is traced against; absent context falls back to
  ``"unknown"``.
* ``step_index`` is a monotonic per-goal counter: the Nth invocation for a
  given goal_id gets index N-1, so ``get_trajectory(goal_id)`` replays steps
  in the order they were executed.
* The recorded step reflects the *last* tool call in the turn: its name and
  args become ``tool_name`` / ``tool_input``, and the matching
  :class:`~langchain_core.messages.ToolMessage` output (by ``tool_call_id``)
  becomes ``tool_output``. A turn with no tool calls records an empty
  ``tool_name`` and the assistant's text as ``thought``.

Recording is strictly best-effort and never breaks the agent run: any store
failure is swallowed and logged. The middleware returns ``None`` from every
hook — it observes, never mutates, the graph state.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, BaseMessage

if TYPE_CHECKING:
    from alpha.trajectory.store import TrajectoryStore

logger = logging.getLogger(__name__)

# Goal id used when the runtime exposes no thread_id (e.g. standalone library
# invocations). Kept as a stable literal so those steps group under one bucket.
_UNKNOWN_GOAL_ID = "unknown"
_MAX_TEXT_CHARS = 4_000
_MAX_TOOL_INPUT_BYTES = 8_192
_MAX_CONTAINER_ITEMS = 32
_MAX_NESTING_DEPTH = 6
_SENSITIVE_KEY_RE = re.compile(r"(?:api[_-]?key|access[_-]?key|secret|password|passwd|token|authorization|credential|private[_-]?key|client[_-]?secret)", re.IGNORECASE)
_ASSIGNMENT_SECRET_RE = re.compile(r"(?i)([A-Za-z0-9_.-]*(?:api[_-]?key|access[_-]?key|secret|password|passwd|token|authorization|credential|private[_-]?key|client[_-]?secret)[A-Za-z0-9_.-]*\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)")
_BEARER_SECRET_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_COMMON_TOKEN_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.DOTALL)
_URL_SECRET_RE = re.compile(r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret|password)=)[^&\s]+")


def _bounded_text(value: str, *, limit: int = _MAX_TEXT_CHARS) -> str:
    """Redact common credential forms and cap text before durable storage."""
    truncated = len(value) > limit
    # Bound regex work as well as persisted output. In particular, credential
    # assignment matching must never scan an untrusted multi-megabyte payload.
    value = value[:limit]
    # Redact authorization schemes before generic key/value assignments so
    # ``Authorization: Bearer <token>`` cannot consume only the word Bearer
    # and leave its credential behind.
    safe = _BEARER_SECRET_RE.sub(r"\1 [REDACTED]", value)
    safe = _ASSIGNMENT_SECRET_RE.sub(r"\1[REDACTED]", safe)
    safe = _COMMON_TOKEN_RE.sub("[REDACTED]", safe)
    safe = _JWT_RE.sub("[REDACTED]", safe)
    safe = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", safe)
    safe = _URL_SECRET_RE.sub(r"\1[REDACTED]", safe)
    if truncated or len(safe) > limit:
        return safe[:limit] + "…[truncated]"
    return safe


def _safe_tool_value(value: Any, *, depth: int = 0) -> Any:
    """Return a JSON-safe, redacted, bounded projection of tool arguments."""
    if depth >= _MAX_NESTING_DEPTH:
        return "[omitted: nesting limit]"
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= _MAX_CONTAINER_ITEMS:
                output["_omitted"] = "additional fields omitted"
                break
            safe_key = _bounded_text(str(key), limit=128)
            output[safe_key] = "[REDACTED]" if _SENSITIVE_KEY_RE.search(safe_key) else _safe_tool_value(child, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        items = [_safe_tool_value(item, depth=depth + 1) for item in value[:_MAX_CONTAINER_ITEMS]]
        if len(value) > _MAX_CONTAINER_ITEMS:
            items.append("[additional items omitted]")
        return items
    if isinstance(value, str):
        return _bounded_text(value, limit=1_024)
    if value is None or isinstance(value, bool | int | float):
        return value
    return _bounded_text(str(value), limit=1_024)


def _bounded_tool_input(value: dict[str, Any]) -> dict[str, Any]:
    safe = _safe_tool_value(value)
    # Container and per-value limits normally keep this below the cap. This
    # final check also covers unusually large keys or non-standard JSON values.
    try:
        import json

        if len(json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= _MAX_TOOL_INPUT_BYTES:
            return safe if isinstance(safe, dict) else {"args": safe}
    except (TypeError, ValueError) as exc:
        logger.debug("Trajectory tool input omitted after JSON serialization failed (%s)", type(exc).__name__)
    return {"_omitted": f"tool arguments exceeded {_MAX_TOOL_INPUT_BYTES} bytes after redaction"}


class TrajectoryRecorderMiddleware(AgentMiddleware[AgentState]):
    """Record one trajectory StepRecord per completed agent turn.

    Args:
        store: The :class:`TrajectoryStore` to append steps to. The Gateway
            supplies ``app.state.trajectory_store``; standalone callers can
            pass any compatible store (or ``get_trajectory_store()``).
    """

    def __init__(self, store: TrajectoryStore) -> None:
        super().__init__()
        self._store = store
        # Per-goal monotonic step counter. Keyed by goal_id so concurrent runs
        # for different threads keep independent indices; a single goal's steps
        # land 0,1,2,... in execution order.
        self._step_index: dict[str, int] = {}

    # ----- extraction helpers ---------------------------------------------

    @staticmethod
    def _goal_id(runtime: Any) -> str:
        """Return the goal id (thread id) for *runtime*, or ``"unknown"``."""
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            thread_id = context.get("thread_id")
            if thread_id:
                return str(thread_id)
        return _UNKNOWN_GOAL_ID

    def _next_index(self, goal_id: str) -> int:
        index = self._step_index.get(goal_id, 0)
        self._step_index[goal_id] = index + 1
        return index

    @staticmethod
    def _last_ai_message(messages: list[BaseMessage]) -> AIMessage | None:
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                return message
        return None

    @staticmethod
    def _tool_output(messages: list[BaseMessage], tool_call_id: str) -> str:
        """Find the ToolMessage answering *tool_call_id* and return its text."""
        if not tool_call_id:
            return ""
        for message in reversed(messages):
            if getattr(message, "tool_call_id", None) == tool_call_id:
                content = getattr(message, "content", "")
                return content if isinstance(content, str) else str(content)
        return ""

    def _record(self, state: AgentState, runtime: Any) -> None:
        """Append one StepRecord for the current turn. Best-effort."""
        try:
            goal_id = self._goal_id(runtime)
            messages: list[BaseMessage] = list(state.get("messages") or [])
            ai_message = self._last_ai_message(messages)

            thought = ""
            tool_name = ""
            tool_input: dict[str, Any] = {}
            tool_output = ""
            if ai_message is not None:
                content = ai_message.content
                thought = _bounded_text(content if isinstance(content, str) else str(content or ""))
                tool_calls = list(ai_message.tool_calls or [])
                if tool_calls:
                    # The last tool call represents the turn's terminal action.
                    last_call = tool_calls[-1]
                    tool_name = str(last_call.get("name") or "")
                    args = last_call.get("args") or {}
                    tool_input = _bounded_tool_input(args if isinstance(args, dict) else {"args": args})
                    tool_output = _bounded_text(self._tool_output(messages, str(last_call.get("id") or "")))

            step_index = self._next_index(goal_id)
            self._store.record_step(
                goal_id,
                step_index,
                thought=thought,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_output=tool_output,
            )
        except Exception:  # noqa: BLE001 - recording must never break the run
            logger.warning("Failed to record trajectory step", exc_info=True)

    # ----- hooks ----------------------------------------------------------

    @override
    def after_agent(self, state: AgentState, runtime: Any) -> dict | None:
        self._record(state, runtime)
        return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Any) -> dict | None:
        self._record(state, runtime)
        return None
