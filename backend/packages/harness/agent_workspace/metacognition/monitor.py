from __future__ import annotations

from typing import Any

from .calibration import ConfidenceCalibrator
from .models import BiasFlag, CognitiveMode, MetacognitiveAssessment


class MetacognitiveMonitor:
    """
    Monitors agent cognitive processes and detects cognitive failure modes.

    Window: last 5 actions (was 3) for stabler signals. New detectors
    (premature convergence, tool misuse) only fire on positive evidence so
    healthy histories stay clean.
    """

    def __init__(self, calibrator: ConfidenceCalibrator | None = None) -> None:
        self.calibrator = calibrator or ConfidenceCalibrator()

    def assess_state(
        self,
        action_history: list[dict[str, Any]],
        current_confidence: float,
        mode: CognitiveMode = CognitiveMode.DELIBERATIVE,
        task_type: str = "general",
    ) -> MetacognitiveAssessment:
        detected_biases: list[BiasFlag] = []
        should_switch = False
        stagnation_score = 0.0

        calibrated_conf = self.calibrator.calibrate(current_confidence, task_type=task_type)
        window = action_history[-5:] if len(action_history) >= 5 else list(action_history)

        # 1. Detect Plan Stagnation: repeated failures (2+ of last 3, or 3+ of last 5)
        if len(action_history) >= 3:
            recent3 = action_history[-3:]
            recent_fails = [a for a in recent3 if not a.get("success", False)]
            window_fails = [a for a in window if not a.get("success", False)]
            if len(recent_fails) >= 2 or len(window_fails) >= 3:
                detected_biases.append(BiasFlag.PLAN_STAGNATION)
                stagnation_score = round(len(recent_fails) / 3.0, 2)
                should_switch = True

        # 2. Detect Overconfidence: claimed confidence > 0.85 despite stagnation
        if current_confidence > 0.85 and BiasFlag.PLAN_STAGNATION in detected_biases:
            detected_biases.append(BiasFlag.OVERCONFIDENCE)
            calibrated_conf = min(calibrated_conf, 0.50)

        # 3. Detect Confirmation Bias: repeating identical tool 3x ending in failure
        if len(action_history) >= 3:
            tools = [a.get("tool") for a in action_history[-3:] if a.get("tool")]
            if len(tools) == 3 and len(set(tools)) == 1 and not action_history[-1].get("success", False):
                detected_biases.append(BiasFlag.CONFIRMATION_BIAS)
                should_switch = True

        # 4. Detect Premature Convergence: 3+ fast successes on identical tool
        # with high confidence and zero verification — only when there is
        # positive evidence of skipped verification, never on diverse tools.
        if len(window) >= 3:
            tools_all = [a.get("tool") for a in window[-3:]]
            succ_all = [bool(a.get("success", False)) for a in window[-3:]]
            verified = any(a.get("verified") or a.get("evidence") for a in window[-3:])
            if len(set(tools_all)) == 1 and all(succ_all) and not verified and current_confidence >= 0.95 and mode == CognitiveMode.FAST:
                detected_biases.append(BiasFlag.PREMATURE_CONVERGENCE)
                should_switch = True

        # 5. Detect Tool Misuse: missing/empty tool names or explicit error markers.
        for action in window[-3:]:
            tool = action.get("tool")
            if tool is None or (isinstance(tool, str) and not tool.strip()):
                if BiasFlag.TOOL_MISUSE not in detected_biases:
                    detected_biases.append(BiasFlag.TOOL_MISUSE)
                    should_switch = True
                break
            if isinstance(action.get("error"), str) and "unknown tool" in action["error"].lower():
                if BiasFlag.TOOL_MISUSE not in detected_biases:
                    detected_biases.append(BiasFlag.TOOL_MISUSE)
                    should_switch = True
                break

        # Formulate actionable recommendation
        if BiasFlag.PLAN_STAGNATION in detected_biases or should_switch:
            if BiasFlag.TOOL_MISUSE in detected_biases:
                rec = "Tool misuse detected. Re-check tool names/schemas before retrying."
            else:
                rec = "Stagnation or bias detected. Recommend switching strategy or requesting peer verification."
        elif BiasFlag.OVERCONFIDENCE in detected_biases:
            rec = "High overconfidence drift detected. Recommend reducing risk tier and running verification."
        else:
            rec = "Cognitive state healthy. Continue nominal execution."

        return MetacognitiveAssessment(
            mode=mode,
            confidence=current_confidence,
            calibrated_confidence=calibrated_conf,
            detected_biases=detected_biases,
            stagnation_score=stagnation_score,
            recommendation=rec,
            should_switch_strategy=should_switch,
        )

    def record_and_assess(
        self,
        action_history: list[dict[str, Any]],
        current_confidence: float,
        actual_success: bool | None = None,
        mode: CognitiveMode = CognitiveMode.DELIBERATIVE,
        task_type: str = "general",
    ) -> MetacognitiveAssessment:
        """Assess then optionally persist the outcome for future calibration."""
        assessment = self.assess_state(
            action_history=action_history,
            current_confidence=current_confidence,
            mode=mode,
            task_type=task_type,
        )
        if actual_success is not None:
            self.calibrator.record_outcome(
                predicted_confidence=current_confidence,
                actual_success=actual_success,
                task_type=task_type,
            )
        return assessment
