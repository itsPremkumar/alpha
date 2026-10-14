"""Tests for the read-only Python compile diagnostics stage."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_python_health.py"


def _load_diagnostics_module():
    spec = importlib.util.spec_from_file_location("check_python_health", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_compile_stage_catches_syntax_errors_with_exact_location(tmp_path: Path, monkeypatch):
    module = _load_diagnostics_module()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n    pass\n", encoding="utf-8")

    exit_code, output = module._compile_stage(["broken.py"])

    assert exit_code == 1
    assert "broken.py:1:" in output
    assert "invalid syntax" in output


def test_compile_stage_does_not_execute_source(tmp_path: Path, monkeypatch):
    module = _load_diagnostics_module()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    marker = tmp_path / "must-not-exist"
    source = tmp_path / "side_effect.py"
    source.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )

    exit_code, output = module._compile_stage(["side_effect.py"])

    assert exit_code == 0
    assert "Compiled 1" in output
    assert not marker.exists()


def test_runtime_smoke_flag_is_available(monkeypatch: pytest.MonkeyPatch):
    module = _load_diagnostics_module()
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--runtime-smoke"])

    args = module._args()

    assert args.runtime_smoke is True


def test_diagnostics_only_flag_is_available(monkeypatch: pytest.MonkeyPatch):
    module = _load_diagnostics_module()
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--diagnostics-only"])

    args = module._args()

    assert args.diagnostics_only is True
