"""Configless-checkout fallback used by the backend test harness."""

from __future__ import annotations

import os
from pathlib import Path

from alpha.config.app_config import AppConfig


def ensure_config_path_for_tests() -> str | None:
    """Use the shipped template only when no operator config resolves."""
    if os.environ.get("ALPHA_CONFIG_PATH"):
        return None

    try:
        AppConfig.resolve_config_path()
    except FileNotFoundError:
        example = Path(__file__).resolve().parents[2] / "config.example.yaml"
        if example.is_file():
            os.environ["ALPHA_CONFIG_PATH"] = str(example)
            return str(example)
        raise
    return None
