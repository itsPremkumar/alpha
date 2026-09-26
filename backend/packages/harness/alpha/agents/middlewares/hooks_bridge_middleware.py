"""Claude/Codex-style shell-hook bridge (DeepSeek-Harness-style hooks engine).

Runs operator-configured ``hooks.json`` command hooks (the Claude ``hooks``
shape) at typed interception points of the agent loop:

- ``SessionStart`` → :meth:`before_agent` / :meth:`abefore_agent`
- ``UserPromptSubmit`` → :meth:`before_model` (prompt text, clipped)
- ``PreToolUse`` → :meth:`wrap_tool_call` / :meth:`awrap_tool_call`
  (tool name + args; may deny the call)
- ``PostToolUse`` → after the tool result (tool name + clipped result)
- ``SessionEnd`` / ``Stop`` → :meth:`after_agent` / :meth:`aafter_agent`

Subset contract (documented, not accidental):
- Only ``{"type": "command", "command": ...}`` entries run; other types are
  skipped with a debug log.
- Matchers: empty/missing matches every tool, otherwise exact name or
  ``fnmatch`` glob (``"*"``, ``"bash|write_file"``-style alternation is
  spelled ``"bash|write_file"`` and matched as a glob; use ``*`` for all).
- Hook stdin receives JSON
  ``{"hook_event_name", "tool_name", "tool_input", "tool_response", "cwd"}``.
- Blocking: process exit code ``2`` blocks with stderr as the model-visible
  reason; exit ``0`` with stdout JSON ``{"decision": "block", "reason": ...}``
  also blocks. Anything else proceeds.
- The hooks file is operator-trusted configuration (commands run with
  Gateway privileges): ``hooks_path`` belongs next to ``config.yaml``,
  never in API-writable state. Disabled (default), missing, or malformed
  file means fail-open passthrough.

Blocking-IO discipline: subprocess execution is synchronous work, so the
async paths always offload through ``asyncio.to_thread`` while the sync
paths call directly — mirroring ``ToolOutputBudgetMiddleware``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from alpha.agents.middlewares.tool_transform_meta import append_tool_transform
from alpha.config.hooks_config import HooksConfig

logger = logging.getLogger(__name__)

_BLOCK_EXIT_CODE = 2
_MAX_PAYLOAD_CHARS = 8_000


@dataclass(frozen=True)
class HookDecision:
    """Outcome of running the hooks for one event."""

    proceed: bool = True
    message: str = ""


@dataclass
class _CachedHooksFile:
    mtime_ns: int = 0
    events: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    warned: bool = False


def _clip(value: object, limit: int = _MAX_PAYLOAD_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} chars omitted]"


def _matcher_matches(matcher: object, tool_name: str) -> bool:
    if matcher is None or (isinstance(matcher, str) and not matcher.strip()):
        return True
    if not isinstance(matcher, str):
        return False
    return any(fnmatch.fnmatchcase(tool_name, part.strip()) for part in matcher.split("|") if part.strip())


def _hook_timeout(entry: dict[str, Any], default_timeout: float) -> float:
    try:
        timeout = float(entry.get("timeout", default_timeout))
    except (TypeError, ValueError):
        return default_timeout
    return timeout if timeout > 0 else default_timeout


class HooksBridgeMiddleware(AgentMiddleware):
    """Execute ``hooks.json`` command hooks at loop interception points."""

    def __init__(self, config: HooksConfig | None = None) -> None:
        super().__init__()
        self._config = config if config is not None else HooksConfig()
        self._cache_lock = threading.Lock()
        self._cache = _CachedHooksFile()

    def release_policy_parameters(self) -> dict[str, object]:
        # Path + budgets only: hook file contents never enter the assembly identity.
        return {
            "enabled": self._config.enabled,
            "hooks_path": self._config.hooks_path,
            "default_timeout_seconds": self._config.default_timeout_seconds,
            "fail_open": self._config.fail_open,
        }

    @classmethod
    def from_app_config(cls, app_config: Any) -> HooksBridgeMiddleware:
        hooks = getattr(app_config, "hooks", None)
        if isinstance(hooks, HooksConfig):
            return cls(config=hooks)
        return cls()

    # -- hooks file loading ------------------------------------------------

    def _load_events(self) -> dict[str, list[dict[str, Any]]]:
        """Parse ``hooks.json`` into ``{event: [command entries]}``, cached by mtime.

        Missing/unreadable/malformed files yield ``{}`` (fail-open
        passthrough); malformed files warn once per mtime so a broken file
        cannot spam the logs once per tool call.
        """
        path = self._config.hooks_path
        if not path:
            return {}
        try:
            mtime_ns = os.path.getmtime(path)
        except OSError:
            return {}
        with self._cache_lock:
            if self._cache.mtime_ns == mtime_ns:
                return self._cache.events
        try:
            with open(path, encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            with self._cache_lock:
                if not self._cache.warned or self._cache.mtime_ns != mtime_ns:
                    logger.warning("Hooks file %r unreadable; hooks pass through (%s)", path, exc)
                self._cache = _CachedHooksFile(mtime_ns=mtime_ns, warned=True)
            return {}
        events: dict[str, list[dict[str, Any]]] = {}
        hooks = document.get("hooks") if isinstance(document, dict) else None
        if isinstance(hooks, dict):
            for event, matchers in hooks.items():
                if not isinstance(matchers, list):
                    continue
                for group in matchers:
                    if not isinstance(group, dict):
                        continue
                    matcher = group.get("matcher")
                    entries = group.get("hooks")
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if not isinstance(entry, dict) or entry.get("type", "command") != "command":
                            logger.debug("Skipping non-command hook entry for event %r", event)
                            continue
                        command = entry.get("command")
                        if not isinstance(command, str) or not command.strip():
                            continue
                        events.setdefault(str(event), []).append({"matcher": matcher, "command": command, "timeout": entry.get("timeout")})
        with self._cache_lock:
            self._cache = _CachedHooksFile(mtime_ns=mtime_ns, events=events)
        return events

    # -- hook execution ----------------------------------------------------

    def _run_command(self, command: str, payload: dict[str, Any], timeout: float) -> tuple[int, str, str]:
        """Run one hook command. Returns ``(returncode, stdout, stderr)`` (both clipped)."""
        try:
            completed = subprocess.run(
                command,
                shell=True,
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return -1, _clip(exc.stdout or ""), f"hook timed out after {timeout:g}s"
        except OSError as exc:
            return -1, "", f"hook failed to start: {exc}"
        return completed.returncode, _clip(completed.stdout or ""), _clip(completed.stderr or "")

    def _decide(self, event: str, *, tool_name: str = "", payload_extra: dict[str, Any] | None = None) -> HookDecision:
        """Run every matching command hook for *event*. First block wins."""
        if not self._config.enabled:
            return HookDecision()
        entries = [entry for entry in self._load_events().get(event, []) if _matcher_matches(entry.get("matcher"), tool_name)]
        if not entries:
            return HookDecision()
        for entry in entries:
            payload = {"hook_event_name": event, "tool_name": tool_name, "cwd": os.getcwd()}
            if payload_extra:
                payload.update(payload_extra)
            returncode, stdout, stderr = self._run_command(entry["command"], payload, _hook_timeout(entry, self._config.default_timeout_seconds))
            if returncode == -1:
                # Timeout or spawn failure: fail open (warn) unless configured closed.
                logger.warning("Hook %r for event %r %s", entry["command"][:80], event, stderr)
                if not self._config.fail_open:
                    return HookDecision(proceed=False, message=f"Hook errored and hooks.fail_open is false: {stderr.strip() or 'unknown error'}")
                continue
            if returncode == _BLOCK_EXIT_CODE:
                reason = stderr.strip() or "blocked by hook"
                logger.info("Hook blocked event %r (tool %r): %s", event, tool_name, reason[:200])
                return HookDecision(proceed=False, message=reason)
            if returncode != 0:
                logger.warning("Hook exited %s for event %r; continuing (only exit 2 blocks)", returncode, event)
                continue
            try:
                verdict = json.loads(stdout) if stdout.strip() else {}
            except ValueError:
                continue
            if isinstance(verdict, dict) and verdict.get("decision") == "block":
                reason = str(verdict.get("reason") or "blocked by hook")
                return HookDecision(proceed=False, message=reason)
        return HookDecision()

    # -- lifecycle hooks (best-effort; never break the run) ----------------

    def _fire_lifecycle(self, event: str) -> None:
        try:
            decision = self._decide(event)
        except Exception:
            logger.warning("Lifecycle hook event %r failed fail-open", event, exc_info=True)
            return
        if not decision.proceed:
            logger.info("Lifecycle hook event %r requested block; lifecycle blocks are advisory only", event)

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._fire_lifecycle("SessionStart")
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        await asyncio.to_thread(self._fire_lifecycle, "SessionStart")
        return None

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._fire_lifecycle("SessionEnd")
        return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        await asyncio.to_thread(self._fire_lifecycle, "SessionEnd")
        return None

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        prompt: str | None = None
        messages = state.get("messages", []) if isinstance(state, dict) else []
        for message in reversed(messages):
            if isinstance(message, HumanMessage) and isinstance(message.content, str) and message.content.strip():
                prompt = message.content
                break
        if prompt is not None:
            try:
                self._decide("UserPromptSubmit", payload_extra={"prompt": _clip(prompt)})
            except Exception:
                logger.warning("UserPromptSubmit hooks failed fail-open", exc_info=True)
        return None

    # -- tool hooks --------------------------------------------------------

    def _blocked_message(self, tool_name: str, tool_call_id: str, reason: str) -> ToolMessage:
        message = ToolMessage(
            content=f"Error: blocked by PreToolUse hook: {reason.strip() or 'no reason given'}",
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )
        new_kwargs = dict(getattr(message, "additional_kwargs", None) or {})
        append_tool_transform(new_kwargs, "hooks_bridge_blocked", by="HooksBridgeMiddleware")
        return message.model_copy(update={"additional_kwargs": new_kwargs})

    def _pre_tool_use(self, tool_name: str, tool_args: Any) -> HookDecision:
        try:
            return self._decide("PreToolUse", tool_name=tool_name, payload_extra={"tool_input": tool_args if isinstance(tool_args, dict) else {}})
        except Exception:
            logger.warning("PreToolUse hooks failed fail-open", exc_info=True)
            return HookDecision()

    def _post_tool_use(self, tool_name: str, result_text: str | None) -> None:
        try:
            self._decide("PostToolUse", tool_name=tool_name, payload_extra={"tool_response": _clip(result_text or "")})
        except Exception:
            logger.warning("PostToolUse hooks failed fail-open", exc_info=True)

    @staticmethod
    def _result_text(result: Any) -> str | None:
        content = getattr(result, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [part for part in content if isinstance(part, str)]
            if parts:
                return "\n".join(parts)
        return None

    @override
    def wrap_tool_call(self, request: ToolCallRequest, handler: Any) -> Any:
        tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
        tool_name = str(tool_call.get("name") or "unknown_tool")
        tool_call_id = str(tool_call.get("id") or "missing-tool-call-id")
        decision = self._pre_tool_use(tool_name, tool_call.get("args") or {})
        if not decision.proceed:
            return self._blocked_message(tool_name, tool_call_id, decision.message)
        result = handler(request)
        self._post_tool_use(tool_name, self._result_text(result))
        return result

    @override
    async def awrap_tool_call(self, request: ToolCallRequest, handler: Any) -> Any:
        tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
        tool_name = str(tool_call.get("name") or "unknown_tool")
        tool_call_id = str(tool_call.get("id") or "missing-tool-call-id")
        decision = await asyncio.to_thread(self._pre_tool_use, tool_name, tool_call.get("args") or {})
        if not decision.proceed:
            return self._blocked_message(tool_name, tool_call_id, decision.message)
        result = await handler(request)
        await asyncio.to_thread(self._post_tool_use, tool_name, self._result_text(result))
        return result
