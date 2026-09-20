"""Metacognitive observer middleware.

Integrated into the lead-agent chain by default (``autonomy.metacognition.enabled``),
and still loadable explicitly via operator config::

    extensions:
      middlewares:
        - alpha.agents.middlewares.metacognitive_middleware:MetacognitiveMiddleware

Observe-only: assesses recent tool history with MetacognitiveMonitor and logs the
recommendation. Never blocks, never mutates state, never changes prompts — so
enabling it cannot alter run topology or cache behavior. The lead loop keeps
working identically with or without it, and any internal failure is swallowed so
observation can never break a run.
"""

from __future__ import annotations

import logging
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware

logger = logging.getLogger(__name__)

# Bounded window of the transcript inspected after each model call.
_TRANSCRIPT_LOOKBACK = 20


class MetacognitiveMiddleware(AgentMiddleware):
    """Observe-only metacognitive health check."""

    def __init__(self, confidence: float = 0.8, window: int = 5) -> None:
        super().__init__()
        self.confidence = confidence
        self.window = max(3, int(window))
        self._history: list[dict[str, Any]] = []

    def record_tool_result(self, tool: str, success: bool) -> dict[str, Any]:
        """Record one tool outcome and return the latest assessment as dict."""
        from alpha.metacognition import CognitiveMode, MetacognitiveMonitor

        self._history.append({"tool": tool, "success": bool(success)})
        self._history = self._history[-50:]
        monitor = MetacognitiveMonitor()
        assessment = monitor.assess_state(
            action_history=self._history[-self.window :],
            current_confidence=self.confidence,
            mode=CognitiveMode.DELIBERATIVE,
        )
        if assessment.should_switch_strategy:
            logger.warning("Metacognitive signal: %s", assessment.recommendation)
        return assessment.to_dict()

    @staticmethod
    def _tool_outcomes(messages: list[Any]) -> list[tuple[str, bool]]:
        """Extract ``(tool_name, succeeded)`` pairs from the tail of the transcript."""
        outcomes: list[tuple[str, bool]] = []
        for message in messages[-_TRANSCRIPT_LOOKBACK:]:
            if type(message).__name__ != "ToolMessage":
                continue
            name = str(getattr(message, "name", "") or "unknown")
            status = str(getattr(message, "status", "") or "").lower()
            content = str(getattr(message, "content", "") or "").lower()
            if status in {"error", "failed"}:
                succeeded = False
            elif status in {"success", "ok"}:
                succeeded = True
            else:
                succeeded = "error" not in content and "traceback" not in content
            outcomes.append((name, succeeded))
        return outcomes

    @override
    def after_model(self, state: Any, runtime: Any) -> None:
        """Observe tool outcomes; fail-open and never mutate state."""
        try:
            messages = state.get("messages") if isinstance(state, dict) else getattr(state, "messages", None)
            if not messages:
                return None
            seen = {entry["tool"] for entry in self._history[-self.window :]}
            for tool, succeeded in self._tool_outcomes(list(messages)):
                if tool in seen:
                    continue
                self.record_tool_result(tool, succeeded)
        except Exception:  # observation must never break a run
            logger.debug("Metacognitive observation skipped", exc_info=True)
        return None
