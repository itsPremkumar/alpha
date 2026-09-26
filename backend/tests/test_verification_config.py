"""Tests for the verification config section."""

from pathlib import Path

import yaml

from alpha.config.verification_config import VerificationConfig


def test_defaults_receipts_on_judge_off():
    config = VerificationConfig()
    assert config.receipts_enabled is True
    assert config.receipts_render_mode == "delegation_only"
    assert config.judge_enabled is False
    assert config.judge_model_name is None


def test_app_config_carries_verification_section():
    from alpha.config.app_config import AppConfig

    app_config = AppConfig.model_validate({"sandbox": {"use": "test"}})
    assert app_config.verification.receipts_enabled is True
    assert app_config.verification.judge_enabled is False


def test_versioned_example_publishes_verification_section():
    example_path = Path(__file__).resolve().parents[2] / "config.example.yaml"
    example = yaml.safe_load(example_path.read_text(encoding="utf-8"))

    assert example["config_version"] >= 34
    # The completion-critic pipeline (Finish-First verifier) is part of the
    # published verification section; the expectation mirrors the example file
    # exactly so a removed/renamed key still fails here.
    assert example["verification"] == {
        "receipts_enabled": True,
        "receipts_render_mode": "delegation_only",
        "judge_enabled": False,
        "judge_model_name": None,
        "completion_critics_enabled": True,
        "completion_critics_require_patch": False,
    }
