"""Hermetic contract tests for the generated-artifact drift gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "scripts" / "check_generated_drift.py"
GENERATOR_PATH = ROOT / "backend" / "scripts" / "generate_feature_manifest.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("alpha_generated_drift_gate_test", GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "agent-workspace"
    workspace.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(workspace))
    return tmp_path


def _make_generator_fixture(tmp_path: Path) -> Path:
    """Create a minimal checkout that uses the real official generator."""
    repo = tmp_path / "fixture-repo"
    backend = repo / "backend"
    (backend / "scripts").mkdir(parents=True)
    (backend / "packages" / "harness" / "alpha" / "tools" / "builtins").mkdir(parents=True)
    (backend / "packages" / "harness" / "alpha" / "agents" / "middlewares").mkdir(parents=True)
    (backend / "app" / "gateway" / "routers").mkdir(parents=True)
    (backend / "app" / "gateway" / "autonomy").mkdir(parents=True)
    (repo / "contracts").mkdir()
    shutil.copy2(GENERATOR_PATH, backend / "scripts" / "generate_feature_manifest.py")

    (backend / "packages" / "harness" / "alpha" / "tools" / "tools.py").write_text("BUILTIN_TOOLS = []\n", encoding="utf-8")
    (backend / "packages" / "harness" / "alpha" / "tools" / "builtins" / "__init__.py").write_text("", encoding="utf-8")
    (backend / "app" / "gateway" / "app.py").write_text(
        "from app.gateway.routers import sample\napp.include_router(sample.router)\n",
        encoding="utf-8",
    )
    (backend / "app" / "gateway" / "routers" / "sample.py").write_text("router = object()\n", encoding="utf-8")
    (backend / "app" / "gateway" / "autonomy" / "supervisor.py").write_text(
        'def register_default_loops():\n    return [("heartbeat", "Heartbeat", lambda: None, 60)]\n',
        encoding="utf-8",
    )

    seed_output = tmp_path / "seed-output"
    result = subprocess.run(
        [sys.executable, str(backend / "scripts" / "generate_feature_manifest.py"), "--output-dir", str(seed_output)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    shutil.copy2(seed_output / "feature_manifest.json", repo / "contracts" / "feature_manifest.json")
    return repo


def _snapshot(repo: Path) -> dict[str, bytes]:
    return {path.relative_to(repo).as_posix(): path.read_bytes() for path in repo.rglob("*") if path.is_file()}


def test_clean_generated_tree_passes_without_repository_writes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    before = _snapshot(repo)

    assert gate.run_gate(repo) == 0

    assert _snapshot(repo) == before
    output = capsys.readouterr().out
    assert "generated artifact drift: 0 file(s)" in output
    assert "docs/INDEX: no committed generated files are present" in output


def test_modified_generated_manifest_fails_with_a_useful_diff(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    manifest_path = repo / "contracts" / "feature_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "tampered"
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")

    assert gate.run_gate(repo) == 1

    output = capsys.readouterr().out
    assert "DRIFT contracts/feature_manifest.json" in output
    assert "changed line(s)" in output
    assert "generated artifact drift: 1 file(s)" in output
    assert "tampered" in output or "version" in output


def test_gate_writes_drift_report_only_outside_the_repository(tmp_path: Path) -> None:
    repo = _make_generator_fixture(tmp_path)
    manifest_path = repo / "contracts" / "feature_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "tampered"
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    report = tmp_path / "artifacts" / "generated-drift.diff"

    assert gate.run_gate(repo, diff_output=report) == 1
    assert report.is_file()
    assert "DRIFT contracts/feature_manifest.json" in report.read_text(encoding="utf-8")


def test_generator_is_invoked_with_the_documented_output_directory_command(tmp_path: Path) -> None:
    output_dir = tmp_path / "scratch"
    command = gate.generator_command(ROOT, output_dir)

    assert Path(command[1]).resolve() == GENERATOR_PATH.resolve()
    assert command[2:] == ["--output-dir", str(output_dir.resolve())]
    assert gate.GENERATOR_COMMAND_DOC == ("python backend/scripts/generate_feature_manifest.py --output-dir <temporary-directory>")
