"""Autonomous Command Middleware for DeerFlow.

Automatically identifies and initiates slash command workflows at the exact correct time in the lifecycle:
1. Pre-turn: Identifies user goal/architecture/research intents and injects autonomous slash directives.
2. Mid-turn: Detects tool failures/crashes and triggers /self-heal recovery workflows.
3. Post-edit: Detects code modifications and triggers /verify and /evidence audit directives.
4. Completion: Detects milestone completion and triggers /learn save reflection directives.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent_workspace.commands.autonomous_engine import (
    AutonomousDetectionResult,
    LifecyclePhase,
    autonomous_command_engine,
)
from agent_workspace.utils.messages import get_original_user_content_text, is_real_user_message

logger = logging.getLogger(__name__)


class AutonomousCommandMiddleware(AgentMiddleware):
    """Intercepts and orchestrates autonomous slash commands at real-time lifecycle trigger points."""

    def __init__(self, confidence_threshold: float = 0.3) -> None:
        self.confidence_threshold = confidence_threshold

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Any],
    ) -> ModelResponse:
        messages = list(request.messages)

        # 1. Analyze latest user message for autonomous slash intent
        latest_user_text = ""
        for msg in reversed(messages):
            if is_real_user_message(msg):
                latest_user_text = get_original_user_content_text(msg)
                break

        directive_injected = False
        if latest_user_text:
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

        # 2. Check for mid-flight errors in tool messages (trigger SELF_HEAL phase)
        if not directive_injected and len(messages) > 0:
            last_msg = messages[-1]
            if isinstance(last_msg, ToolMessage) and ("error" in str(last_msg.content).lower() or "traceback" in str(last_msg.content).lower()):
                heal_detection = autonomous_command_engine.trigger_phase_transition(
                    LifecyclePhase.SELF_HEAL,
                    details=f"Tool error encountered: {str(last_msg.content)[:200]}",
                )
                heal_msg = self._build_directive_message(heal_detection)
                messages.append(heal_msg)
                logger.info("Autonomous self-heal triggered for tool error.")

        # 3. Check for code edit tool completion (trigger VERIFICATION phase)
        elif not directive_injected and len(messages) > 0:
            last_msg = messages[-1]
            if isinstance(last_msg, ToolMessage):
                tool_name = getattr(last_msg, "name", "").lower()
                if any(k in tool_name for k in ["write", "edit", "patch", "replace", "code"]):
                    verify_detection = autonomous_command_engine.trigger_phase_transition(
                        LifecyclePhase.VERIFICATION,
                        details=f"Code modified via {tool_name}. Verification required.",
                    )
                    verify_msg = self._build_directive_message(verify_detection)
                    messages.append(verify_msg)
                    logger.info("Autonomous verification triggered after code edit.")

        # Forward request with any autonomous directives applied
        modified_request = request.override(messages=messages)
        return await handler(modified_request)

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
