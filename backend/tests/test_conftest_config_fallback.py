"""The test session resolves a config even in a checkout with no ``config.yaml``.

CI (and a fresh clone) provision no operator config — it is gitignored and no
workflow writes one — but the single-file model config made module-scope
registry bindings call ``get_app_config()`` during test *collection*. The
conftest helper points ``ALPHA_CONFIG_PATH`` at the shipped
``config.example.yaml`` in exactly that case and only that case:

- an explicit ``ALPHA_CONFIG_PATH`` is an operator assertion and is never
  overridden (a missing file there must keep raising, per the config guide);
- a real ``config.yaml`` still wins, so a developer's own config keeps driving
  their local runs exactly as before.
"""

from __future__ import annotations

import os
from pathlib import Path

import conftest as conftest_module

from alpha.config.app_config import AppConfig


def _shipped_template() -> Path:
    return Path(conftest_module.__file__).resolve().parents[2] / "config.example.yaml"


def test_explicit_config_path_is_left_alone(monkeypatch):
    monkeypatch.setenv("ALPHA_CONFIG_PATH", str(Path("does") / "not" / "exist.yaml"))
    assert conftest_module.ensure_config_path_for_tests() is None
    assert os.environ["ALPHA_CONFIG_PATH"].endswith("exist.yaml")


def test_existing_config_resolution_keeps_env_unset(monkeypatch):
    monkeypatch.delenv("ALPHA_CONFIG_PATH", raising=False)
    monkeypatch.setattr(AppConfig, "resolve_config_path", classmethod(lambda cls: Path("resolved") / "config.yaml"))
    assert conftest_module.ensure_config_path_for_tests() is None
    assert "ALPHA_CONFIG_PATH" not in os.environ


def test_missing_config_falls_back_to_shipped_template(monkeypatch):
    monkeypatch.delenv("ALPHA_CONFIG_PATH", raising=False)

    def _missing(cls):
        raise FileNotFoundError("`config.yaml` file not found in the project root")

    monkeypatch.setattr(AppConfig, "resolve_config_path", classmethod(_missing))
    resolved = conftest_module.ensure_config_path_for_tests()

    assert resolved == str(_shipped_template())
    assert resolved is not None and Path(resolved).is_file()
    assert os.environ["ALPHA_CONFIG_PATH"] == resolved
