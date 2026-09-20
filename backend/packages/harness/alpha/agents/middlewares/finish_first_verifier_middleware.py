"""Finish-First Evidence Verification Middleware.

Synthesized from Claude Fable 5.1 & NVIDIA AVO:
Guarantees that when code modifications occur, the agent either verifies them
empirically via test suites or explicitly reports the verification status.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

_WRITE_TOOLS = frozenset({"write_file", "str_replace", "hashline_edit"})
_VERIFY_TOOLS = frozenset({
    "auto_test_and_repair",
    "reproduce_and_verify",
    "run_task_evaluation_benchmark",
    "audit_finish_first_evidence",
})
_TEST_KEYWORDS = ("pytest", "npm test", "pnpm test", "cargo test", "go test", "python -m unittest")


class FinishFirstVerifierMiddleware(AgentMiddleware[AgentState]):
    """Middleware that audits the evidence matrix for code modifications."""

    def __init__(self, enabled: bool = True) -> None:
        super().__init__()
        self.enabled = enabled

    def after_model(
        self,
        state: AgentState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
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

        # If code was modified and no verification tool was executed, stamp an evidence notice
        if had_code_writes and not had_verification:
            content = last_ai.content
            if isinstance(content, str) and "[Finish-First Notice]" not in content:
                notice = (
                    "\n\n> [!NOTE]\n"
                    "> **[Finish-First Notice]**: Code modifications were performed in this session without an automated test verification step. "
                    "Run `auto_test_and_repair` to verify tests before deploying."
                )
                updated_ai = AIMessage(
                    content=content + notice,
                    id=last_ai.id,
                    additional_kwargs=last_ai.additional_kwargs,
                    response_metadata=last_ai.response_metadata,
                )
                return {"messages": [updated_ai]}

        return None
