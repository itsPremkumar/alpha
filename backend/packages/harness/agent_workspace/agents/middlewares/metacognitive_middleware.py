"""Opt-in metacognitive observer middleware (not auto-registered).

Enable via operator config only::

    extensions:
      middlewares:
        - deerflow.agents.middlewares.metacognitive_middleware:MetacognitiveMiddleware

Observe-only: assesses recent tool history with MetacognitiveMonitor and logs
the recommendation. Never blocks, never mutates state, never changes prompts —
so enabling it cannot alter run topology or cache behavior. The lead loop keeps
working identically with or without it.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MetacognitiveMiddleware:
    """Observe-only metacognitive health check."""

    def __init__(self, confidence: float = 0.8, window: int = 5) -> None:
        self.confidence = confidence
        self.window = max(3, int(window))
        self._history: list[dict[str, Any]] = []

    def record_tool_result(self, tool: str, success: bool) -> dict[str, Any]:
        """Record one tool outcome and return the latest assessment as dict."""
        from agent_workspace.metacognition import CognitiveMode, MetacognitiveMonitor

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

    # LangGraph middleware hook (observe-only — always returns None).
    def after_model(self, state: Any, runtime: Any) -> dict | None:  # noqa: ANN401
        return None
