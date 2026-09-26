
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


def test_brier_score_is_none_without_records():
    """No records means no measurement — not 0.0, which would claim perfection."""
    calibrator = ConfidenceCalibrator()
    assert calibrator.compute_brier_score() is None
    stats = calibrator.stats()
    assert stats["brier_score"] is None
    assert stats["total_records"] == 0
    assert stats["dropped_records"] == 0

    # A task_type with no matching records is also unmeasured.
    calibrator.record_outcome(predicted_confidence=0.5, actual_success=True, task_type="coding")
    assert calibrator.compute_brier_score(task_type="research") is None
    assert calibrator.compute_brier_score(task_type="coding") is not None


def test_corrupt_persisted_records_are_logged_and_disclosed(caplog):
    """Corrupt records are dropped with a counted warning + dropped_records field."""
    import logging

    data = {
        "max_records": 10,
        "records": [
            {"predicted_confidence": 0.9, "actual_success": True, "task_type": "general"},
            {"predicted_confidence": "not-a-number", "actual_success": True},
            "not-an-object",
        ],
    }
    with caplog.at_level(logging.WARNING, logger="alpha.metacognition.calibration"):
        calibrator = ConfidenceCalibrator.from_dict(data)

    assert len(calibrator.records) == 1
    assert calibrator.dropped_records == 2
    assert calibrator.stats()["dropped_records"] == 2
    assert any(
        "Dropped 2 corrupt calibration record(s)" in rec.getMessage() for rec in caplog.records
    ), "drop count must be logged, not silent"

    # The disclosure survives a save/load round trip.
    reloaded = ConfidenceCalibrator.from_dict(calibrator.to_dict())
    assert reloaded.dropped_records == 2
