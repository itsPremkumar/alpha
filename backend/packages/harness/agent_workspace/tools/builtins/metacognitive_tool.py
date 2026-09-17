"""Built-in metacognitive tool inspired by hermes-agi-asi-harness."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.metacognition import (
    CognitiveMode,
    MetacognitiveMonitor,
)

_GLOBAL_METACONGITIVE_MONITOR = MetacognitiveMonitor()


@tool("check_metacognitive_health", parse_docstring=True)
def check_metacognitive_health(
    action_history_json: str,
    current_confidence: float = 0.8,
    cognitive_mode: str = "deliberative",
    task_type: str = "general",
) -> str:
    """Assess metacognitive health, detect cognitive biases, and calibrate confidence.

    Monitors for confirmation bias, premature convergence, plan stagnation,
    tool misuse, and overconfidence drift.

    Args:
        action_history_json: JSON array of recent action dicts containing {'tool': str, 'success': bool}.
        current_confidence: Claimed confidence score (0.0 to 1.0).
        cognitive_mode: Current cognitive mode ('fast', 'deliberative', 'research', 'exploratory', 'adversarial', 'recovery').
        task_type: Optional task family for calibrated confidence (e.g. coding, research).
    """
    try:
        history = json.loads(action_history_json) if action_history_json else []
    except Exception:
        history = []

    mode_map = {
        "fast": CognitiveMode.FAST,
        "deliberative": CognitiveMode.DELIBERATIVE,
        "research": CognitiveMode.RESEARCH,
        "exploratory": CognitiveMode.EXPLORATORY,
        "adversarial": CognitiveMode.ADVERSARIAL,
        "recovery": CognitiveMode.RECOVERY,
    }
    mode = mode_map.get(cognitive_mode.lower().strip(), CognitiveMode.DELIBERATIVE)

    assessment = _GLOBAL_METACONGITIVE_MONITOR.assess_state(
        action_history=history,
        current_confidence=current_confidence,
        mode=mode,
        task_type=task_type or "general",
    )

    return json.dumps(assessment.to_dict(), indent=2)
