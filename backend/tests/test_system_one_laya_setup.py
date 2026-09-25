"""Unit tests for the project-local Laya setup helper.

These tests intentionally never install a package, download weights, or start a
server. The actual setup is an explicit operator command; the helper's path and
model-selection logic must remain testable offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.system_one_laya_setup import (
    LAYA_VERSION,
    expand_models,
    load_runtime_config,
    resolve_torch_index,
    runtime_environment,
    save_runtime_config,
    venv_python,
)


def test_single_checkpoint_selection_is_bounded():
    assert expand_models("english") == (["english"], "english")
    assert expand_models("typed-decisions") == (["typed-decisions"], "typed-decisions")


def test_router_selection_preloads_language_pair_and_uses_empty_model():
    assert expand_models("router") == (["english", "multilingual"], "")


def test_unknown_checkpoint_is_rejected():
    with pytest.raises(ValueError, match="unknown Laya model"):
        expand_models("not-a-checkpoint")


def test_runtime_environment_keeps_weights_inside_project_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "global-cache"))
    env = runtime_environment(tmp_path, models=["english"], device="cpu", host="127.0.0.1", port=8000)

    assert env["HF_HOME"] == str((tmp_path / "hf-cache").resolve())
    assert env["LAYA_MODELS"] == "english"
    assert env["LAYA_DEVICE"] == "cpu"
    assert env["LAYA_HOST"] == "127.0.0.1"
    assert env["LAYA_PORT"] == "8000"
    assert env["LAYA_PRELOAD"] == "1"
    assert env["USE_TF"] == "0"


def test_auto_runtime_environment_lets_laya_select_cuda_or_cpu(monkeypatch, tmp_path):
    monkeypatch.setenv("LAYA_DEVICE", "cpu")
    env = runtime_environment(tmp_path, models=["english"], device="auto", host="127.0.0.1", port=8000)

    assert "LAYA_DEVICE" not in env


def test_auto_torch_index_prefers_cuda_when_nvidia_smi_is_available(monkeypatch):
    monkeypatch.setattr("scripts.system_one_laya_setup.shutil.which", lambda name: "nvidia-smi")
    monkeypatch.setattr(
        "scripts.system_one_laya_setup.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "GPU 0"})(),
    )

    assert resolve_torch_index("auto").endswith("/cu126")


def test_auto_torch_index_falls_back_to_cpu_without_nvidia_smi(monkeypatch):
    monkeypatch.setattr("scripts.system_one_laya_setup.shutil.which", lambda name: None)

    assert resolve_torch_index("auto").endswith("/cpu")


def test_runtime_environment_loads_only_laya_secrets_from_project_dotenv(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text('LAYA_API_KEY="local-secret"\nHF_TOKEN=hf-token\nOPENROUTER_API_KEY=do-not-copy\n', encoding="utf-8")
    monkeypatch.setattr("scripts.system_one_laya_setup.PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("LAYA_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    env = runtime_environment(tmp_path / "runtime", models=["english"], device="cpu", host="127.0.0.1", port=8000)

    assert env["LAYA_API_KEY"] == "local-secret"
    assert env["HF_TOKEN"] == "hf-token"
    assert "OPENROUTER_API_KEY" not in env
    assert "AI_GATEWAY_API_KEY" not in env


def test_runtime_descriptor_round_trips_without_secrets(tmp_path):
    path = save_runtime_config(
        tmp_path,
        models=["english"],
        alpha_model="english",
        device="cpu",
        host="127.0.0.1",
        port=8000,
        python=venv_python(tmp_path),
    )
    data = load_runtime_config(tmp_path)

    assert path.is_file()
    assert data["package"] == "laya"
    assert data["package_version"] == LAYA_VERSION
    assert data["alpha_model"] == "english"
    assert "api_key" not in data


def test_windows_and_posix_venv_paths_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.system_one_laya_setup.os.name", "nt")
    assert venv_python(tmp_path) == Path(tmp_path) / ".venv" / "Scripts" / "python.exe"
    monkeypatch.setattr("scripts.system_one_laya_setup.os.name", "posix")
    assert venv_python(tmp_path) == tmp_path / ".venv" / "bin" / "python"
