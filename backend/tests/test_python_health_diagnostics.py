"""Tests for the read-only Python compile diagnostics stage."""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
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


def test_streaming_runtime_stage_shows_progress_and_keeps_a_full_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_diagnostics_module()
    reports = tmp_path / "reports"
    reports.mkdir()
    log_path = reports / "pytest-smoke.log"
    result_rows = []

    command = [
        sys.executable,
        "-c",
        "import time; print('runtime check started', flush=True); time.sleep(0.4); print('runtime check finished', flush=True)",
    ]
    worker = threading.Thread(
        target=lambda: result_rows.append(
            module._run_process_stage(
                "pytest-smoke",
                command,
                cwd=tmp_path,
                reports_dir=reports,
                stream_output=True,
            )
        ),
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 2
    try:
        while time.monotonic() < deadline:
            if log_path.exists() and "runtime check started" in log_path.read_text(encoding="utf-8"):
                break
            time.sleep(0.01)
        else:
            pytest.fail("runtime progress was not written to the stage log before exit")
        assert worker.is_alive(), "the child exited before the test observed streamed progress"
    finally:
        worker.join(timeout=5)

    assert not worker.is_alive()
    result = result_rows[0]
    assert result.ok is True
    assert "runtime check finished" in log_path.read_text(encoding="utf-8")
    assert "runtime check started" in capsys.readouterr().out


def test_streaming_runtime_stage_keeps_failure_output_and_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_diagnostics_module()
    reports = tmp_path / "reports"
    reports.mkdir()

    result = module._run_process_stage(
        "pytest-failure",
        [
            sys.executable,
            "-c",
            "import sys; print('assertion failure details', flush=True); sys.exit(7)",
        ],
        cwd=tmp_path,
        reports_dir=reports,
        stream_output=True,
    )

    assert result.ok is False
    assert result.exit_code == 7
    assert "assertion failure details" in result.output_tail
    assert "assertion failure details" in (reports / "pytest-failure.log").read_text(encoding="utf-8")
    assert "assertion failure details" in capsys.readouterr().out
