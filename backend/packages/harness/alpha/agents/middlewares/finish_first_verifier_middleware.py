"""Finish-First Evidence Verification Middleware.

Synthesized from Claude Fable 5.1 & NVIDIA AVO:
Guarantees that when code modifications occur, the agent either verifies them
empirically via test suites or explicitly reports the verification status.

This is also where the completion critics (``alpha.critic``) are wired into the
agent. That package shipped complete -- ``CriticPipeline``,
``AgentFinishedCritic``, ``EmptyPatchCritic``, ``RubricEvaluator`` -- with its
own test suite and *no caller*, so a terminal message was never checked against
the turn's own tool history: an agent whose last action failed could declare
success and the run would end. A terminal message is the one claim the agent
makes about the whole turn, so it is exactly the claim worth verifying in code
rather than asking for in a rubric.

On rejection the terminal message is withdrawn and the agent is sent back with
the critic's ``diagnostic_prompt`` (same mechanism as
``TerminalResponseMiddleware``: ``RemoveMessage`` + ``jump_to: "model"`` plus a
hidden reminder injected into the next model call). The retry is budgeted once
per run so a critic can never create a loop, and a critic that raises is logged
and ignored -- an added guard must not be able to take down a run.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.runtime import Runtime

from alpha.agents.middlewares._bounded_dict import BoundedDict
from alpha.critic import CriticPipeline

logger = logging.getLogger(__name__)

_WRITE_TOOLS = frozenset({"write_file", "str_replace", "hashline_edit"})
_VERIFY_TOOLS = frozenset({
    "auto_test_and_repair",
    "reproduce_and_verify",
    "run_task_evaluation_benchmark",
    "audit_finish_first_evidence",
})
_TEST_KEYWORDS = ("pytest", "npm test", "pnpm test", "cargo test", "go test", "python -m unittest")

_CRITIC_REJECTION_NAME = "completion_critic_rejection"
_FINISH_FIRST_NOTICE = (
    "\n\n> [!NOTE]\n"
    "> **[Finish-First Notice]**: Code modifications were performed in this session without an automated test verification step. "
    "Run `auto_test_and_repair` to verify tests before deploying."
)


def _content_text(content: Any) -> str:
    """Flatten message content to plain text for the critics' keyword checks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts)
    return ""


def _exit_code(content: Any) -> int | None:
    """Best-effort non-zero exit code from a JSON tool result.

    ``AgentFinishedCritic`` reads ``exit_code`` off each history entry, but a
    tool that shells out reports the code inside a JSON payload rather than in
    ``ToolMessage.status``, so read it from there when it is present.
    """
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("exit_code")
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _execution_history(turn_messages: list[Any]) -> list[dict[str, Any]]:
    """Shape the turn's tool results the way ``BaseCritic`` subclasses read them."""
    history: list[dict[str, Any]] = []
    for message in turn_messages:
        if not isinstance(message, ToolMessage):
            continue
        text = _content_text(message.content)
        status = getattr(message, "status", "success")
        entry: dict[str, Any] = {
            "tool_name": getattr(message, "name", "") or "",
            "status": status,
            "content": text,
            "exit_code": _exit_code(text),
        }
        if status == "error":
            entry["error"] = text or "tool call failed"
        history.append(entry)
    return history


class FinishFirstVerifierMiddleware(AgentMiddleware[AgentState]):
    """Middleware that audits the evidence matrix for code modifications."""

    def __init__(self, enabled: bool = True, critic_pipeline: CriticPipeline | None = None) -> None:
        super().__init__()
        self.enabled = enabled
        self.critic_pipeline = critic_pipeline
        self._lock = threading.Lock()
        self._critic_retries: BoundedDict[tuple[str, str], int] = BoundedDict(1000)
        self._pending_prompts: BoundedDict[tuple[str, str], str] = BoundedDict(1000)

    @staticmethod
    def _key(runtime: Runtime) -> tuple[str, str]:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            thread_id = str(context.get("thread_id") or "unknown-thread")
            run_id = str(context.get("run_id") or context.get("run_attempt_id") or id(runtime))
            return thread_id, run_id
        # Defensive fallback for tests/custom embeddings. Production Gateway runs
        # always provide thread_id and run_id in Runtime.context.
        return "unknown-thread", str(id(runtime))

    def _reset(self, runtime: Runtime) -> None:
        key = self._key(runtime)
        with self._lock:
            self._critic_retries.pop(key, None)
            self._pending_prompts.pop(key, None)

    def _clear_other_runs(self, runtime: Runtime) -> None:
        thread_id, run_id = self._key(runtime)
        with self._lock:
            stale = [key for key in self._critic_retries if key[0] == thread_id and key[1] != run_id]
            for key in stale:
                self._critic_retries.pop(key, None)
                self._pending_prompts.pop(key, None)

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        self._clear_other_runs(runtime)
        self._reset(runtime)
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        self._clear_other_runs(runtime)
        self._reset(runtime)
        return None

    @hook_config(can_jump_to=["model"])
    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Inspect the model response. If terminal, verify evidence integrity."""
        if not self.enabled:
            return None

        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None

        last_ai = messages[-1]
        # If the model is continuing tool execution, do not intercept
        if last_ai.tool_calls or getattr(last_ai, "invalid_tool_calls", None):
            return None

        # Find the latest user message boundary
        latest_user_idx = -1
        for i, m in enumerate(messages):
            if isinstance(m, HumanMessage):
                latest_user_idx = i

        turn_messages = messages[latest_user_idx + 1 :] if latest_user_idx >= 0 else messages

        # Scan for code write operations and verification operations in this turn
        had_code_writes = False
        had_verification = False

        for m in turn_messages:
            if isinstance(m, ToolMessage):
                tool_name = getattr(m, "name", "")
                if tool_name in _WRITE_TOOLS:
                    had_code_writes = True
                elif tool_name in _VERIFY_TOOLS:
                    had_verification = True
                elif tool_name == "bash":
                    content_str = str(m.content).lower()
                    if any(kw in content_str for kw in _TEST_KEYWORDS):
                        had_verification = True

        # Verify the terminal claim against the turn's own tool history first: a
        # rejected claim is withdrawn and retried, so the notice below would be
        # annotating a message that is about to be discarded.
        rejection = self._rejection(messages, turn_messages, runtime, latest_user_idx)
        if rejection is not None:
            return rejection

        # If code was modified and no verification tool was executed, stamp an evidence notice
        if had_code_writes and not had_verification:
            content = last_ai.content
            if isinstance(content, str) and "[Finish-First Notice]" not in content:
                updated_ai = AIMessage(
                    content=content + _FINISH_FIRST_NOTICE,
                    id=last_ai.id,
                    additional_kwargs=last_ai.additional_kwargs,
                    response_metadata=last_ai.response_metadata,
                )
                return {"messages": [updated_ai]}

        return None

    @hook_config(can_jump_to=["model"])
    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self.after_model(state, runtime)

    def _rejection(
        self,
        messages: list[Any],
        turn_messages: list[Any],
        runtime: Runtime,
        latest_user_idx: int,
    ) -> dict[str, Any] | None:
        """Return the retry state update when a critic rejects the terminal claim."""
        if self.critic_pipeline is None:
            return None

        last_ai = messages[-1]
        # ``RemoveMessage`` needs the id to withdraw the claim. Without one the
        # retry would re-read the very claim it is meant to discard, so let the
        # completion stand rather than spin.
        if not last_ai.id:
            logger.warning("FinishFirstVerifier: terminal message has no id; skipping critic check")
            return None

        key = self._key(runtime)
        with self._lock:
            attempts = self._critic_retries.get(key, 0)
        if attempts >= 1:
            # One retry per run. A second rejection would mean the agent cannot
            # satisfy the critic, and looping is worse than an unverified finish.
            return None

        task_description = _content_text(messages[latest_user_idx].content) if latest_user_idx >= 0 else ""
        context = getattr(runtime, "context", None)
        workspace_dir = context.get("workspace_dir") if isinstance(context, dict) else None

        try:
            result = self.critic_pipeline.evaluate(
                task_description=task_description,
                execution_history=_execution_history(turn_messages),
                workspace_dir=str(workspace_dir) if workspace_dir else None,
            )
        except Exception:
            # An added guard must never be able to take down a run.
            logger.exception("FinishFirstVerifier: critic pipeline raised; accepting completion")
            return None

        if not result.is_rejected:
            if result.verdict.value == "warning":
                logger.info("FinishFirstVerifier: completion critic warning: %s", result.reason)
            return None

        with self._lock:
            self._critic_retries[key] = attempts + 1
            self._pending_prompts[key] = result.diagnostic_prompt or result.reason
        logger.warning("FinishFirstVerifier: completion rejected: %s", result.reason)
        return {"messages": [RemoveMessage(id=last_ai.id)], "jump_to": "model"}

    def _augment_request(self, request: ModelRequest) -> ModelRequest:
        key = self._key(request.runtime)
        with self._lock:
            prompt = self._pending_prompts.pop(key, None)
        if not prompt:
            return request
        reminder = HumanMessage(
            content=f"<system_reminder>\n{prompt}\n</system_reminder>",
            name=_CRITIC_REJECTION_NAME,
            additional_kwargs={"hide_from_ui": True},
        )
        return request.override(messages=[*request.messages, reminder])

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._augment_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._augment_request(request))
