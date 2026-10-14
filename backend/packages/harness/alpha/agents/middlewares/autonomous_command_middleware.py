"""Autonomous Command Middleware for Alpha.

Automatically identifies and initiates slash command workflows at the exact correct time in the lifecycle:
1. Pre-turn: Identifies user goal/architecture/research intents and injects autonomous slash directives.
2. Mid-turn: Detects tool failures/crashes and triggers /self-heal recovery workflows.
3. Post-edit: Detects code modifications and triggers /verify and /evidence audit directives.
4. Completion: Detects milestone completion and triggers /learn save reflection directives.

Both the sync and the async model-call seams are implemented.  ``AgentMiddleware``
raises ``NotImplementedError`` from the base ``wrap_model_call`` when only
``awrap_model_call`` is overridden, which makes every synchronous ``invoke()`` /
``stream()`` run of an agent carrying this middleware fail outright.  The
detection logic lives in one pure method so the two seams cannot drift.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage, ToolMessage

from alpha.agents.middlewares.edit_detection import is_source_code_edit_result
from alpha.agents.middlewares.tool_result_meta import TOOL_META_KEY
from alpha.commands.autonomous_engine import (
    AutonomousDetectionResult,
    LifecyclePhase,
    autonomous_command_engine,
)
from alpha.utils.messages import get_original_user_content_text, is_real_user_message

logger = logging.getLogger(__name__)


class AutonomousCommandMiddleware(AgentMiddleware):
    """Intercepts and orchestrates autonomous slash commands at real-time lifecycle trigger points."""

    def __init__(self, confidence_threshold: float = 0.3) -> None:
        self.confidence_threshold = confidence_threshold
        self._processed_user_turns: dict[str, None] = {}
        self._processed_user_turns_lock = threading.Lock()

    def _apply_lifecycle_directives(self, request: ModelRequest) -> ModelRequest:
        """Return *request* with any autonomous directive messages inserted.

        Pure with respect to the request: the message list is copied before any
        insertion, so the caller's request is never mutated.  Shared verbatim by
        the sync and async seams.
        """
        messages = list(request.messages)

        # 1. Analyze latest user message for autonomous slash intent
        latest_user_text = ""
        latest_user_message: Any | None = None
        for msg in reversed(messages):
            if is_real_user_message(msg):
                latest_user_message = msg
                latest_user_text = get_original_user_content_text(getattr(msg, "content", ""), getattr(msg, "additional_kwargs", None))
                break

        directive_injected = False
        if latest_user_text and latest_user_message is not None and self._claim_user_turn(latest_user_message):
            detection = autonomous_command_engine.identify_and_trigger(
                latest_user_text,
                auto_execute=True,
            )
            if detection.matched and detection.confidence >= self.confidence_threshold:
                directive_msg = self._build_directive_message(detection)
                # Inject directive into message stream
                messages.insert(len(messages) - 1, directive_msg)
                directive_injected = True
                logger.info(
                    "Autonomous command triggered: %s (phase: %s, confidence: %.2f)",
                    detection.command,
                    detection.phase.value,
                    detection.confidence,
                )

        # 2. Check structured tool failure metadata (trigger SELF_HEAL phase).
        # Tool result prose is untrusted: words like "error" can appear in a
        # successful report, and copying arbitrary tool output into a
        # SystemMessage would promote it to trusted instruction authority.
        if not directive_injected and len(messages) > 0:
            last_msg = messages[-1]
            if isinstance(last_msg, ToolMessage):
                additional_kwargs = getattr(last_msg, "additional_kwargs", {})
                tool_meta = additional_kwargs.get(TOOL_META_KEY) if isinstance(additional_kwargs, Mapping) else None
                structured_failure = isinstance(tool_meta, Mapping) and tool_meta.get("status") == "error"
                recoverable = isinstance(tool_meta, Mapping) and tool_meta.get("recoverable_by_model") is True
                legacy_failure = not isinstance(tool_meta, Mapping) and getattr(last_msg, "status", None) == "error"
                if (structured_failure and recoverable) or legacy_failure:
                    tool_name = str(getattr(last_msg, "name", "") or "tool")[:80]
                    error_type = str(tool_meta.get("error_type") or "unknown")[:80] if isinstance(tool_meta, Mapping) else "unknown"
                    heal_detection = autonomous_command_engine.trigger_phase_transition(
                        LifecyclePhase.SELF_HEAL,
                        details=f"Recoverable tool failure in {tool_name} (error type: {error_type}).",
                    )
                    heal_msg = self._build_directive_message(heal_detection)
                    messages.append(heal_msg)
                    logger.info("Autonomous self-heal triggered for structured recoverable tool failure.")

        # 3. Check for code edit tool completion (trigger VERIFICATION phase)
        if not directive_injected and len(messages) > 0:
            last_msg = messages[-1]
            if isinstance(last_msg, ToolMessage) and not ("error" in str(last_msg.content).lower() or "traceback" in str(last_msg.content).lower()):
                if is_source_code_edit_result(messages, last_msg):
                    tool_name = getattr(last_msg, "name", "").lower()
                    verify_detection = autonomous_command_engine.trigger_phase_transition(
                        LifecyclePhase.VERIFICATION,
                        details=f"Code modified via {tool_name}. Verification required.",
                    )
                    verify_msg = self._build_directive_message(verify_detection)
                    messages.append(verify_msg)
                    logger.info("Autonomous verification triggered after code edit.")

        if not directive_injected and len(messages) == len(request.messages):
            return request
        return request.override(messages=messages)

    def _claim_user_turn(self, message: Any) -> bool:
        """Run natural-language command detection once per real user message.

        The same HumanMessage remains in graph state for every model/tool cycle.
        Without this per-turn claim, a command can execute and its directive be
        injected again on every cycle, duplicating work and preventing the
        agent from reaching a final response.
        """
        message_id = getattr(message, "id", None)
        key = f"id:{message_id}" if message_id else f"object:{id(message)}"
        with self._processed_user_turns_lock:
            if key in self._processed_user_turns:
                return False
            self._processed_user_turns[key] = None
            if len(self._processed_user_turns) > 256:
                self._processed_user_turns.pop(next(iter(self._processed_user_turns)))
            return True

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Any],
    ) -> ModelResponse:
        """Synchronous model-call seam (agent ``invoke()`` / ``stream()``)."""
        return handler(self._apply_lifecycle_directives(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Any],
    ) -> ModelResponse:
        return await handler(self._apply_lifecycle_directives(request))

    def _build_directive_message(self, detection: AutonomousDetectionResult) -> SystemMessage:
        directives_text = "\n".join(f"  * {d}" for d in detection.autonomous_directives)
        content = (
            f"<autonomous_command_lifecycle_directive>\n"
            f"PHASE: {detection.phase.value.upper()}\n"
            f"COMMAND IDENTIFIED: {detection.command}\n"
            f"CONFIDENCE: {detection.confidence}\n"
            f"REASON: {detection.reason}\n"
            f"AUTONOMOUS ACTIONS:\n{directives_text}\n"
            f"INSTRUCTION: You must immediately execute and align with this autonomous directive. "
            f"Use the execute_slash_command tool if required, and guarantee all proof obligations are satisfied.\n"
            f"</autonomous_command_lifecycle_directive>"
        )
        return SystemMessage(content=content)
