
from alpha.metacognition import (
    BiasFlag,
    CognitiveMode,
    ConfidenceCalibrator,
    MetacognitiveMonitor,
)


def test_confidence_calibrator_overconfidence_shrinkage():
    calibrator = ConfidenceCalibrator()

    # Record 4 predictions with high confidence (0.95) but low actual success rate (25%)
    calibrator.record_outcome(predicted_confidence=0.95, actual_success=True)
    calibrator.record_outcome(predicted_confidence=0.95, actual_success=False)
    calibrator.record_outcome(predicted_confidence=0.95, actual_success=False)
    calibrator.record_outcome(predicted_confidence=0.95, actual_success=False)

    stats = calibrator.stats()
    assert stats["total_records"] == 4
    assert stats["brier_score"] > 0.3

    # Calibrating 0.90 should apply conservative shrinkage
    calibrated = calibrator.calibrate(raw_confidence=0.90)
    assert calibrated < 0.90


def test_metacognitive_monitor_plan_stagnation_and_overconfidence():
    monitor = MetacognitiveMonitor()

    action_history = [
        {"tool": "bash", "action": "run_test", "success": False},
        {"tool": "bash", "action": "run_test", "success": False},
        {"tool": "bash", "action": "run_test", "success": False},
    ]

    assessment = monitor.assess_state(
        action_history=action_history,
        current_confidence=0.95,
        mode=CognitiveMode.DELIBERATIVE,
    )

    assert BiasFlag.PLAN_STAGNATION in assessment.detected_biases
    assert BiasFlag.OVERCONFIDENCE in assessment.detected_biases
    assert BiasFlag.CONFIRMATION_BIAS in assessment.detected_biases
    assert assessment.should_switch_strategy is True
    assert assessment.calibrated_confidence <= 0.50


def test_metacognitive_monitor_healthy_state():
    monitor = MetacognitiveMonitor()

    action_history = [
        {"tool": "view_file", "action": "read", "success": True},
        {"tool": "replace_content", "action": "edit", "success": True},
        {"tool": "pytest", "action": "verify", "success": True},
    ]

    assessment = monitor.assess_state(
        action_history=action_history,
        current_confidence=0.88,
        mode=CognitiveMode.FAST,
    )

    assert len(assessment.detected_biases) == 0
    assert assessment.should_switch_strategy is False
    assert "healthy" in assessment.recommendation.lower()
