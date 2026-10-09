"""Tests for the managed browser-use runtime and its two agent-facing tools.

Everything here is offline: the install steps, the version probe and the agent
subprocess are all mocked, because a test that downloads Chromium to assert
"pip ran" is a test nobody runs. The runner script is exercised separately by
``TestRunnerScript`` against a fake ``browser_use`` module injected into
``sys.modules``, which is the one place the child-side protocol is actually
tested end to end.

Two properties are pinned deliberately, because both are easy to regress into
dishonesty:

* ``status()`` reports what an import *proves*, never what the marker file
  claims — a marker can outlive a deleted package directory.
* a failed envelope leads with the reason. A subprocess that died and a task
  browser-use could not finish are different facts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from alpha.community.browser_use import manager as manager_mod
from alpha.community.browser_use import tools as tools_mod
from alpha.community.browser_use.manager import (
    MAX_MAX_STEPS,
    MAX_RUN_TIMEOUT_SECONDS,
    BrowserUseError,
    BrowserUseManager,
    redact_secrets,
)


def _config_with(models, default_model=None):
    """A real AppConfig built from lightweight model entries.

    ``sandbox`` is required by the schema, and ``extensions`` is read from disk
    unless a scratch ``ALPHA_EXTENSIONS_CONFIG_PATH`` is set — both are needed
    for the file to load hermetically.
    """
    import yaml

    from alpha.config.app_config import AppConfig

    built_entries = []
    for entry in models:
        entry_dict = {"name": entry["name"], "model": entry.get("model", "x")}
        if entry.get("use"):
            entry_dict["use"] = entry["use"]
        built_entries.append(entry_dict)

    payload = {"sandbox": {"use": "alpha.sandbox.local:LocalSandboxProvider"}, "models": built_entries}
    if default_model:
        payload["default_model"] = default_model

    with tempfile.TemporaryDirectory() as tmp:
        extensions_path = Path(tmp) / "extensions.json"
        extensions_path.write_text(json.dumps({"mcpServers": {}, "skills": {}, "middlewares": []}), encoding="utf-8")
        config_path = Path(tmp) / "config.yaml"
        config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        with _patched_env("ALPHA_EXTENSIONS_CONFIG_PATH", str(extensions_path)):
            return AppConfig.from_file(str(config_path))


@contextlib.contextmanager
def _patched_env(key: str, value: str):
    previous = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def _model_entry(name, *, use=None, model="x"):
    return {"name": name, "use": use, "model": model}


def _runtime():
    return SimpleNamespace(context={"thread_id": "thread-1"}, state={"thread_data": {}})


def _message(result) -> str:
    return result.update["messages"][0].content


def _installed_manager(tmp_path: Path, version: str = "0.9.9") -> BrowserUseManager:
    mgr = BrowserUseManager(tmp_path / "venv")
    mgr.python_path.parent.mkdir(parents=True, exist_ok=True)
    mgr.python_path.write_text("#!/bin/sh\n", encoding="utf-8")
    mgr._write_marker(version)
    return mgr


def _completed(stdout: str, stderr: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["python"], returncode=code, stdout=stdout, stderr=stderr)


class TestVenvResolution:
    def test_default_venv_lives_under_runtime_home(self, tmp_path):
        with patch.object(manager_mod, "runtime_home", return_value=tmp_path):
            mgr = BrowserUseManager()
        assert mgr.venv_dir == tmp_path / "browser-use"

    def test_explicit_path_is_resolved_and_made_absolute(self, tmp_path):
        mgr = BrowserUseManager(tmp_path / "custom" / "venv")
        assert mgr.venv_dir == (tmp_path / "custom" / "venv").resolve()
        assert mgr.venv_dir.is_absolute()

    def test_python_path_matches_platform_layout(self, tmp_path):
        mgr = BrowserUseManager(tmp_path / "venv")
        assert mgr.python_path.parent.name == ("Scripts" if sys.platform == "win32" else "bin")

    def test_runner_script_ships_next_to_the_manager(self, tmp_path):
        assert BrowserUseManager(tmp_path / "venv").runner_path.is_file()
        assert BrowserUseManager(tmp_path / "venv").runner_path.name == "runner.py"


class TestVersionProbe:
    def test_probe_returns_none_when_python_missing(self, tmp_path):
        assert BrowserUseManager(tmp_path / "venv").probe_version() is None

    def test_probe_reads_version_from_import(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        with patch.object(subprocess, "run", return_value=_completed(json.dumps({"version": "1.2.3"}))):
            assert mgr.probe_version() == "1.2.3"

    def test_probe_fails_closed_on_nonzero_exit(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        with patch.object(subprocess, "run", return_value=_completed("", "ModuleNotFoundError: browser_use", code=1)):
            assert mgr.probe_version() is None

    def test_probe_fails_closed_on_malformed_output(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        with patch.object(subprocess, "run", return_value=_completed("not json at all")):
            assert mgr.probe_version() is None

    def test_probe_returns_none_on_oserror(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        with patch.object(subprocess, "run", side_effect=OSError("no interpreter")):
            assert mgr.probe_version() is None


class TestStatusHonesty:
    def test_status_reports_uninstalled_without_marker(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        with patch.object(subprocess, "run", return_value=_completed("", "boom", code=1)):
            status = mgr.status()
        assert status.installed is False
        assert "NOT installed" in status.summary()

    def test_marker_alone_never_claims_installed(self, tmp_path):
        """A marker file can survive a deleted venv; only an import proves it."""
        mgr = _installed_manager(tmp_path)
        assert mgr.marker_path.is_file()
        with patch.object(subprocess, "run", side_effect=OSError("interpreter gone")):
            assert mgr.status().installed is False

    def test_marker_version_is_not_reported_as_the_running_version(self, tmp_path):
        mgr = _installed_manager(tmp_path, version="9.9.9")
        with patch.object(subprocess, "run", side_effect=OSError("interpreter gone")):
            status = mgr.status()
        assert status.installed is False
        assert status.installed_at is not None
        assert "9.9.9" not in status.summary()


class TestEnsureInstalled:
    def test_creates_venv_then_installs_and_probes(self, tmp_path):
        mgr = BrowserUseManager(tmp_path / "venv")
        calls: list[list[str]] = []
        state = {"pip_done": False}

        def fake_run(argv, **kwargs):
            args = [str(a) for a in argv]
            calls.append(args)
            joined = " ".join(args)
            if "venv" in args:
                mgr.python_path.parent.mkdir(parents=True, exist_ok=True)
                mgr.python_path.write_text("#!/bin/sh\n", encoding="utf-8")
                return _completed("")
            if "pip" in args:
                state["pip_done"] = True
                return _completed("Successfully installed browser-use-1.4.0")
            if "playwright" in joined:
                return _completed("Chromium downloaded")
            if "import browser_use" in joined:
                # A fresh venv cannot import browser-use until pip has run; a
                # fake that always answers here would skip the install entirely.
                if not state["pip_done"]:
                    return _completed("", "ModuleNotFoundError: browser_use", code=1)
                return _completed(json.dumps({"version": "1.4.0"}))
            return _completed("ok")

        with patch.object(subprocess, "run", side_effect=fake_run):
            status = mgr.ensure_installed()

        assert status.installed is True
        assert status.version == "1.4.0"
        assert mgr.marker_path.is_file()
        assert any("install" in " ".join(call) and "browser-use" in " ".join(call) for call in calls)
        assert any("playwright" in call and "chromium" in call for call in calls)

    def test_already_installed_skips_pip(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        seen: list[list[str]] = []

        def fake_run(argv, **kwargs):
            seen.append([str(a) for a in argv])
            return _completed(json.dumps({"version": "1.4.0"}))

        with patch.object(subprocess, "run", side_effect=fake_run):
            status = mgr.ensure_installed()

        assert status.version == "1.4.0"
        assert not any("pip" in call for call in seen), "an installed browser-use must not be reinstalled on every run"

    def test_missing_litellm_repairs_existing_runtime(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        seen: list[str] = []

        def fake_run(argv, **kwargs):
            cmd = " ".join(str(a) for a in argv)
            seen.append(cmd)
            if "import browser_use" in cmd:
                return _completed(json.dumps({"version": "1.4.0"}))
            if "importlib.metadata" in cmd and "litellm" in cmd:
                return _completed("PackageNotFoundError", code=1)
            if "freeze" in cmd:
                return _completed("browser-use==1.4.0\n")
            return _completed("ok")

        with patch.object(subprocess, "run", side_effect=fake_run):
            mgr.ensure_installed()

        assert any("pip install litellm" in cmd for cmd in seen)

    def test_upgrade_forces_pip_install(self, tmp_path):
        mgr = _installed_manager(tmp_path)

        def fake_run(argv, **kwargs):
            if "import browser_use" in str(argv[-1]):
                return _completed(json.dumps({"version": "2.0.0"}))
            return _completed("ok")

        with patch.object(subprocess, "run", side_effect=fake_run):
            status = mgr.ensure_installed(upgrade=True)

        assert status.version == "2.0.0"

    def test_venv_creation_failure_names_the_cause(self, tmp_path):
        mgr = BrowserUseManager(tmp_path / "venv")
        with patch.object(subprocess, "run", return_value=_completed("", "no ensurepip", code=1)), pytest.raises(BrowserUseError, match="venv"):
            mgr.ensure_installed()

    def test_pip_failure_is_reported_not_swallowed(self, tmp_path):
        mgr = _installed_manager(tmp_path)

        def fake_run(argv, **kwargs):
            if "import browser_use" in str(argv[-1]):
                return _completed(json.dumps({"version": "1.0.0"}))
            return _completed("", "ERROR: could not resolve browser-use", code=1)

        with patch.object(subprocess, "run", side_effect=fake_run), pytest.raises(BrowserUseError, match="Installing browser-use failed"):
            mgr.ensure_installed(upgrade=True)

    def test_chromium_download_failure_is_reported(self, tmp_path):
        """Only reachable on a Playwright-backed venv — see the CDP test below."""

        def fake_run(argv, **kwargs):
            cmd = " ".join(str(a) for a in argv)
            if "import browser_use" in cmd:
                return _completed(json.dumps({"version": "1.0.0"}))
            if "importlib.metadata" in cmd and "playwright" in cmd:
                return _completed("")  # playwright IS present -> we fetch a browser
            if "playwright" in cmd:
                return _completed("", "browser download failed", code=1)
            return _completed("ok")

        mgr = _installed_manager(tmp_path)
        with patch.object(subprocess, "run", side_effect=fake_run), pytest.raises(BrowserUseError, match="Chromium"):
            mgr.ensure_installed(upgrade=True)

    def test_install_that_cannot_import_afterwards_fails_loudly(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        with patch.object(subprocess, "run", return_value=_completed("ok")), pytest.raises(BrowserUseError, match="still cannot be imported"):
            mgr.ensure_installed(upgrade=True)

    def test_extra_packages_are_installed_after_browser_use_with_constraints(self, tmp_path):
        """browser-use is installed alone, then extras under its resolved pins.

        Found by installing for real: browser-use 0.13.x pins ``openai==2.26.0``
        while current ``langchain-openai`` wants a newer SDK. One combined pip
        command resolves to the newer SDK and leaves the venv silently broken,
        so the extras must be a separate, constrained step.
        """
        mgr = _installed_manager(tmp_path)
        seen: list[str] = []

        def fake_run(argv, **kwargs):
            cmd = " ".join(str(a) for a in argv)
            seen.append(cmd)
            if "import browser_use" in cmd:
                return _completed(json.dumps({"version": "1.0.0"}))
            if "freeze" in cmd:
                return _completed("openai==2.26.0\nbrowser-use==0.13.11\n")
            return _completed("ok")

        with patch.object(subprocess, "run", side_effect=fake_run):
            mgr.ensure_installed(upgrade=True, extra_packages=["langchain-openai"])

        install_idx = next(i for i, cmd in enumerate(seen) if "install" in cmd and "browser-use" in cmd and "langchain-openai" not in cmd)
        extra_idx = next(i for i, cmd in enumerate(seen) if "langchain-openai" in cmd)
        assert install_idx < extra_idx, "browser-use must be installed before the extras it constrains"
        assert "--constraint" in seen[extra_idx]

    def test_conflicting_extra_fails_the_install_loudly(self, tmp_path):
        """pip only *warns* about a broken dependency set, so `pip check` gates it."""
        mgr = _installed_manager(tmp_path)

        def fake_run(argv, **kwargs):
            cmd = " ".join(str(a) for a in argv)
            if "import browser_use" in cmd:
                return _completed(json.dumps({"version": "1.0.0"}))
            if "freeze" in cmd:
                return _completed("openai==2.26.0\n")
            if "check" in cmd:
                return _completed("browser-use 0.13.11 has requirement openai==2.26.0, but you have openai 3.26.1", code=1)
            return _completed("ok")

        with patch.object(subprocess, "run", side_effect=fake_run), pytest.raises(BrowserUseError, match="unsatisfiable dependency set"):
            mgr.ensure_installed(upgrade=True, extra_packages=["langchain-openai"])

    def test_missing_playwright_is_not_an_install_failure(self, tmp_path):
        """0.13.x drives the host's Chrome over CDP; there is no browser to fetch."""
        mgr = _installed_manager(tmp_path)

        def fake_run(argv, **kwargs):
            cmd = " ".join(str(a) for a in argv)
            if "import browser_use" in cmd:
                return _completed(json.dumps({"version": "1.0.0"}))
            if "importlib.metadata" in cmd and "playwright" in cmd:
                return _completed("PackageNotFoundError", code=1)
            return _completed("ok")

        with patch.object(subprocess, "run", side_effect=fake_run):
            status = mgr.ensure_installed(upgrade=True)

        assert status.installed is True
        assert any("no playwright" in entry for entry in status.log)

    def test_playwright_venv_does_download_a_browser(self, tmp_path):
        mgr = _installed_manager(tmp_path)

        def fake_run(argv, **kwargs):
            cmd = " ".join(str(a) for a in argv)
            if "import browser_use" in cmd:
                return _completed(json.dumps({"version": "1.0.0"}))
            if "importlib.metadata" in cmd and "playwright" in cmd:
                return _completed("")
            return _completed("ok")

        with patch.object(subprocess, "run", side_effect=fake_run):
            mgr.ensure_installed(upgrade=True)

        assert mgr.marker_path.is_file()


class TestRedact:
    def test_redacts_a_long_secret(self):
        assert "sk-secret-value" not in redact_secrets("failed with sk-secret-value", "sk-secret-value")

    def test_ignores_short_or_empty_secrets(self):
        assert redact_secrets("abc", "abc") == "abc"
        assert redact_secrets("none", None) == "none"


class TestRunEnvelope:
    @staticmethod
    @contextlib.contextmanager
    def _running(tmp_path, proc):
        mgr = _installed_manager(tmp_path)
        with patch.object(mgr, "probe_version", return_value="1.4.0"), patch.object(subprocess, "Popen", return_value=proc):
            yield mgr

    def test_parses_last_json_line_from_chatty_stdout(self, tmp_path):
        proc = MagicMock()
        proc.communicate.return_value = ('Downloading Chromium...\nlog noise\n{"ok": true, "result": "done", "steps": 3, "history": []}\n', "")
        with self._running(tmp_path, proc) as mgr:
            envelope = mgr.run(task="check the site", llm_spec={"use": "langchain_openai:ChatOpenAI"})
        assert envelope["ok"] is True
        assert envelope["result"] == "done"
        assert envelope["steps"] == 3

    def test_missing_envelope_is_an_honest_failure(self, tmp_path):
        proc = MagicMock()
        proc.communicate.return_value = ("", "Traceback: boom")
        with self._running(tmp_path, proc) as mgr:
            envelope = mgr.run(task="check the site", llm_spec={})
        assert envelope["ok"] is False
        assert "no result envelope" in envelope["error"]
        assert "Traceback" in envelope["error"]

    def test_api_key_is_redacted_from_a_failure(self, tmp_path):
        proc = MagicMock()
        proc.communicate.return_value = ("", "auth failed for sk-live-abcdef123456")
        with self._running(tmp_path, proc) as mgr:
            envelope = mgr.run(task="t", llm_spec={"api_key": "sk-live-abcdef123456"})
        assert "sk-live-abcdef123456" not in envelope["error"]

    def test_timeout_returns_timed_out_envelope_not_a_crash(self, tmp_path):
        proc = MagicMock()
        proc.communicate.side_effect = [subprocess.TimeoutExpired(cmd="python", timeout=300), ("", "")]
        with self._running(tmp_path, proc) as mgr, patch.object(BrowserUseManager, "_kill_tree") as kill:
            envelope = mgr.run(task="t", llm_spec={}, timeout_seconds=300)
        assert envelope["ok"] is False
        assert envelope["timed_out"] is True
        kill.assert_called_once()

    def test_not_installed_refuses_instead_of_running(self, tmp_path):
        mgr = BrowserUseManager(tmp_path / "venv")
        with patch.object(mgr, "probe_version", return_value=None), pytest.raises(BrowserUseError, match="not installed"):
            mgr.run(task="t", llm_spec={})

    def test_start_url_is_forwarded(self, tmp_path):
        proc = MagicMock()
        proc.communicate.return_value = ('{"ok": true, "result": "", "steps": 0, "history": []}', "")
        with self._running(tmp_path, proc) as mgr, patch.object(mgr, "_scratch_dir", return_value=tmp_path):
            mgr.run(task="t", llm_spec={}, start_url="https://example.com")
        payload = json.loads(proc.communicate.call_args.kwargs["input"])
        assert payload["start_url"] == "https://example.com"


class TestLLMSpec:
    def _config(self, models, providers=None, default_model="m1"):
        cfg = MagicMock()
        cfg.models = models
        cfg.providers = providers or {}
        cfg.default_model_name = default_model
        return cfg

    def _model(self, name, **kwargs):
        m = SimpleNamespace(name=name, provider=kwargs.pop("provider", None))
        m.model_dump = lambda **kw: {"name": name, **kwargs}
        return m

    def test_resolves_declared_model(self):
        model = self._model("m1", use="langchain_openai:ChatOpenAI", model="gpt-x", api_key="k1", base_url="https://api.test")
        with patch.object(manager_mod, "get_app_config", return_value=self._config([model])):
            spec = manager_mod.resolve_llm_spec()
        assert spec["use"] == "langchain_openai:ChatOpenAI"
        assert spec["model_name"] == "gpt-x"
        assert spec["base_url"] == "https://api.test"

    def test_provider_profile_is_inherited(self):
        model = self._model("m1", provider="p1", use="langchain_openai:ChatOpenAI", model="gpt-x")
        profile = SimpleNamespace(name="p1")
        profile.model_dump = lambda **kw: {"name": "p1", "base_url": "https://profile.test", "api_key": "pk"}
        with patch.object(manager_mod, "get_app_config", return_value=self._config([model], {"p1": profile})):
            spec = manager_mod.resolve_llm_spec()
        assert spec["base_url"] == "https://profile.test"
        assert spec["api_key"] == "pk"

    def test_model_level_key_wins_over_profile(self):
        model = self._model("m1", provider="p1", use="langchain_openai:ChatOpenAI", model="gpt-x", api_key="model-key")
        profile = SimpleNamespace(name="p1")
        profile.model_dump = lambda **kw: {"name": "p1", "api_key": "profile-key"}
        with patch.object(manager_mod, "get_app_config", return_value=self._config([model], {"p1": profile})):
            spec = manager_mod.resolve_llm_spec()
        assert spec["api_key"] == "model-key"

    def test_unknown_model_is_refused(self):
        with patch.object(manager_mod, "get_app_config", return_value=self._config([self._model("m1")])):  # noqa: SIM117
            with pytest.raises(BrowserUseError, match="not declared"):
                manager_mod.resolve_llm_spec("nope")

    def test_unknown_provider_is_refused(self):
        model = self._model("m1", provider="missing", use="langchain_openai:ChatOpenAI", model="gpt-x")
        with patch.object(manager_mod, "get_app_config", return_value=self._config([model])):
            with pytest.raises(BrowserUseError, match="does not declare"):
                manager_mod.resolve_llm_spec()

    def test_model_without_use_is_refused(self):
        model = self._model("m1", model="gpt-x")
        with patch.object(manager_mod, "get_app_config", return_value=self._config([model])):
            with pytest.raises(BrowserUseError, match="no `use:`"):
                manager_mod.resolve_llm_spec()


class TestToolBoundaries:
    @pytest.mark.asyncio
    async def test_setup_reports_version(self):
        mgr = MagicMock()
        status = manager_mod.BrowserUseStatus(installed=True, version="1.4.0", venv_path="/v", python_path="/v/python")
        mgr.ensure_installed.return_value = status
        with patch.object(tools_mod, "get_browser_use_manager", return_value=mgr), patch.object(tools_mod, "_get_tool_config", return_value={}):
            result = await tools_mod.browser_use_setup_tool.coroutine(runtime=_runtime(), tool_call_id="c1")
        assert "1.4.0" in _message(result)

    @pytest.mark.asyncio
    async def test_setup_reports_install_failure(self):
        mgr = MagicMock()
        mgr.ensure_installed.side_effect = BrowserUseError("pip exploded")
        with patch.object(tools_mod, "get_browser_use_manager", return_value=mgr), patch.object(tools_mod, "_get_tool_config", return_value={}):
            result = await tools_mod.browser_use_setup_tool.coroutine(runtime=_runtime(), tool_call_id="c1")
        assert "pip exploded" in _message(result)

    @pytest.mark.asyncio
    async def test_run_rejects_empty_task(self):
        with patch.object(tools_mod, "_get_tool_config", return_value={}):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="   ", tool_call_id="c1")
        assert "must not be empty" in _message(result)

    @pytest.mark.asyncio
    async def test_run_rejects_oversized_task(self):
        with patch.object(tools_mod, "_get_tool_config", return_value={}):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="x" * (manager_mod.MAX_TASK_CHARS + 1), tool_call_id="c1")
        assert "over the" in _message(result)

    @pytest.mark.asyncio
    async def test_run_refuses_steps_above_ceiling(self):
        with patch.object(tools_mod, "_get_tool_config", return_value={}):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="do it", tool_call_id="c1", max_steps=MAX_MAX_STEPS + 1)
        assert f"over the {MAX_MAX_STEPS}-step ceiling" in _message(result)

    @pytest.mark.asyncio
    async def test_run_refuses_timeout_above_ceiling(self):
        cfg = {"timeout_seconds": MAX_RUN_TIMEOUT_SECONDS + 1}
        with patch.object(tools_mod, "_get_tool_config", return_value=cfg):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="do it", tool_call_id="c1")
        assert f"over the {MAX_RUN_TIMEOUT_SECONDS}s ceiling" in _message(result)

    @pytest.mark.asyncio
    async def test_run_rejects_private_start_url(self):
        with patch.object(tools_mod, "_get_tool_config", return_value={}), patch.object(tools_mod, "validate_public_http_url", return_value="private address refused"):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="do it", tool_call_id="c1", start_url="http://127.0.0.1:8080/admin")
        assert "URL policy" in _message(result)

    @pytest.mark.asyncio
    async def test_run_refuses_when_not_installed_and_auto_install_off(self):
        """A usable model is required before the install question is even asked.

        The shipped default is `alpha-free`, so this test names a provider-backed
        model to reach the not-installed refusal at all.
        """
        mgr = MagicMock()
        mgr.status.return_value = manager_mod.BrowserUseStatus(installed=False, version=None, venv_path="/v", python_path="/v/python")
        cfg = {"auto_install": False}
        with (
            patch.object(tools_mod, "get_browser_use_manager", return_value=mgr),
            patch.object(tools_mod, "_get_tool_config", return_value=cfg),
            patch.object(tools_mod, "_resolve_and_check_spec", return_value={"use": "langchain_openai:ChatOpenAI", "model_name": "m"}),
        ):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="do it", tool_call_id="c1")
        assert "not installed" in _message(result)

    @pytest.mark.asyncio
    async def test_run_installs_on_demand_then_runs(self):
        mgr = MagicMock()
        mgr.status.return_value = manager_mod.BrowserUseStatus(installed=False, version=None, venv_path="/v", python_path="/v/python")
        mgr.run.return_value = {"ok": True, "result": "the answer", "steps": 2, "history": [{"url": "https://a.test"}], "version": "1.4.0"}
        with patch.object(tools_mod, "get_browser_use_manager", return_value=mgr), patch.object(tools_mod, "_get_tool_config", return_value={}), patch.object(tools_mod, "resolve_llm_spec", return_value={"use": "x:Y"}):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="do it", tool_call_id="c1")
        assert mgr.ensure_installed.called
        assert mgr.run.called
        assert "the answer" in _message(result)
        assert "https://a.test" in _message(result)

    @pytest.mark.asyncio
    async def test_failed_envelope_leads_with_the_reason(self):
        mgr = MagicMock()
        mgr.status.return_value = manager_mod.BrowserUseStatus(installed=True, version="1.4.0", venv_path="/v", python_path="/v/python")
        mgr.run.return_value = {"ok": False, "error": "login required", "steps": None, "history": []}
        with patch.object(tools_mod, "get_browser_use_manager", return_value=mgr), patch.object(tools_mod, "_get_tool_config", return_value={}), patch.object(tools_mod, "resolve_llm_spec", return_value={}):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="do it", tool_call_id="c1")
        text = _message(result)
        assert "did NOT complete" in text
        assert "login required" in text

    @pytest.mark.asyncio
    async def test_silent_success_does_not_read_as_an_answer(self):
        mgr = MagicMock()
        mgr.status.return_value = manager_mod.BrowserUseStatus(installed=True, version="1.4.0", venv_path="/v", python_path="/v/python")
        mgr.run.return_value = {"ok": True, "result": "  ", "steps": 1, "history": []}
        with patch.object(tools_mod, "get_browser_use_manager", return_value=mgr), patch.object(tools_mod, "_get_tool_config", return_value={}), patch.object(tools_mod, "resolve_llm_spec", return_value={}):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="do it", tool_call_id="c1")
        assert "without producing any text" in _message(result)

    @pytest.mark.asyncio
    async def test_long_result_is_truncated_with_a_marker(self):
        mgr = MagicMock()
        mgr.status.return_value = manager_mod.BrowserUseStatus(installed=True, version="1.4.0", venv_path="/v", python_path="/v/python")
        mgr.run.return_value = {"ok": True, "result": "y" * 20000, "steps": 1, "history": []}
        with patch.object(tools_mod, "get_browser_use_manager", return_value=mgr), patch.object(tools_mod, "_get_tool_config", return_value={}), patch.object(tools_mod, "resolve_llm_spec", return_value={}):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="do it", tool_call_id="c1")
        assert "[truncated:" in _message(result)

    @pytest.mark.asyncio
    async def test_manager_error_is_reported_not_raised(self):
        mgr = MagicMock()
        mgr.status.return_value = manager_mod.BrowserUseStatus(installed=True, version="1.4.0", venv_path="/v", python_path="/v/python")
        mgr.run.side_effect = BrowserUseError("no venv")
        with patch.object(tools_mod, "get_browser_use_manager", return_value=mgr), patch.object(tools_mod, "_get_tool_config", return_value={}), patch.object(tools_mod, "resolve_llm_spec", return_value={}):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="do it", tool_call_id="c1")
        assert "no venv" in _message(result)


class TestGovernanceDeclaration:
    def test_setup_is_execute_risk_and_asks(self):
        meta = tools_mod.browser_use_setup_tool.metadata
        assert meta["governance_risk_class"] == "execute"
        assert meta["governance_confirmation"] == "ask"

    def test_run_is_external_and_unknown_reversibility(self):
        meta = tools_mod.browser_use_run_tool.metadata
        assert meta["governance_risk_class"] == "external"
        assert meta["governance_reversibility"] == "unknown"
        assert meta["governance_confirmation"] == "ask"


class TestLiteLLMRouting:
    """LiteLLM needs `provider/model`; Alpha stores the bare provider slug.

    Found by a real run: `unbiased/pareto` + an OpenRouter base_url produced
    "litellm.BadRequestError: LLM Provider NOT provided" on *every* step, so the
    agent burned its whole budget and answered nothing.
    """

    def test_openrouter_host_gets_its_prefix(self):
        from alpha.community.browser_use.runner import _litellm_model_id

        assert _litellm_model_id("unbiased/pareto", "https://openrouter.ai/api/v1") == "openrouter/unbiased/pareto"

    def test_anthropic_host_gets_its_prefix(self):
        from alpha.community.browser_use.runner import _litellm_model_id

        assert _litellm_model_id("claude-3-5-sonnet", "https://api.anthropic.com/v1") == "anthropic/claude-3-5-sonnet"

    def test_unknown_host_falls_back_to_openai_compatible(self):
        from alpha.community.browser_use.runner import _litellm_model_id

        assert _litellm_model_id("some-model", "https://vireonix.ai/v1") == "openai/some-model"
        assert _litellm_model_id("some-model", None) == "openai/some-model"

    def test_already_qualified_model_is_not_double_prefixed(self):
        from alpha.community.browser_use.runner import _litellm_model_id

        assert _litellm_model_id("openrouter/unbiased/pareto", "https://openrouter.ai/api/v1") == "openrouter/unbiased/pareto"
        assert _litellm_model_id("anthropic/claude-3", "https://api.anthropic.com") == "anthropic/claude-3"

    def test_keyless_endpoint_gets_a_placeholder_key(self, monkeypatch):
        """LiteLLM refuses to build a client with no api_key at all."""
        from alpha.community.browser_use import runner as runner_mod

        captured = {}

        class FakeChatLiteLLM:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setitem(sys.modules, "browser_use.llm.litellm.chat", SimpleNamespace(ChatLiteLLM=FakeChatLiteLLM))
        runner_mod._try_native_llm({"model_name": "auto", "base_url": "https://vireonix.ai/v1"}, "auto")
        assert captured["api_key"] == "not-required"
        assert captured["api_base"] == "https://vireonix.ai/v1"

    def test_real_key_is_preferred_over_the_placeholder(self, monkeypatch):
        from alpha.community.browser_use import runner as runner_mod

        captured = {}

        class FakeChatLiteLLM:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setitem(sys.modules, "browser_use.llm.litellm.chat", SimpleNamespace(ChatLiteLLM=FakeChatLiteLLM))
        runner_mod._try_native_llm({"api_key": "sk-real", "base_url": "https://openrouter.ai/api/v1"}, "unbiased/pareto")
        assert captured["api_key"] == "sk-real"


class TestDefaultExtraPackages:
    def test_litellm_is_part_of_installed(self):
        """Without it every model call fails inside the agent loop."""
        assert "litellm" in manager_mod.DEFAULT_EXTRA_PACKAGES

    def test_readiness_uses_metadata_not_an_import_probe(self, tmp_path):
        """Regression: `import litellm` measured 127s, so an import-based probe
        reported a good install as missing - and 'missing' drives a reinstall, so
        every call then paid a full pip install until it timed out at 900s."""
        mgr = _installed_manager(tmp_path)

        def fake_run(argv, **kwargs):
            cmd = " ".join(str(a) for a in argv)
            if "import browser_use" in cmd:
                return _completed(json.dumps({"version": "1.0.0"}))
            if "importlib.metadata" in cmd:
                return _completed("1.0.0")
            return _completed("ok")

        with patch.object(subprocess, "run", side_effect=fake_run), patch.object(BrowserUseManager, "_probe_import", side_effect=AssertionError("import probe must not gate readiness")) as import_probe:
            status = mgr.ensure_installed(upgrade=False, extra_packages=[])

        assert status.installed is True
        assert not import_probe.called

    def test_probe_distribution_uses_metadata(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        seen: list[list[str]] = []

        def fake_run(argv, **kwargs):
            seen.append([str(a) for a in argv])
            return _completed("0.0.1")

        with patch.object(subprocess, "run", side_effect=fake_run):
            assert mgr.probe_distribution("litellm") is True
        # Metadata lookup, never a heavy import of the package itself.
        assert any("importlib.metadata" in " ".join(cmd) for cmd in seen)
        assert not any("import litellm" in " ".join(cmd) for cmd in seen)

    def test_probe_distribution_is_false_when_absent(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        with patch.object(subprocess, "run", return_value=_completed("", "PackageNotFoundError", code=1)):
            assert mgr.probe_distribution("litellm") is False

    def test_install_requests_litellm(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        seen: list[str] = []

        def fake_run(argv, **kwargs):
            cmd = " ".join(str(a) for a in argv)
            seen.append(cmd)
            if "import browser_use" in cmd:
                return _completed(json.dumps({"version": "1.0.0"}))
            if "freeze" in cmd:
                return _completed("openai==2.26.0\n")
            return _completed("ok")

        with patch.object(subprocess, "run", side_effect=fake_run):
            mgr.ensure_installed(upgrade=True)

        assert any("litellm" in cmd for cmd in seen)

    def test_missing_litellm_triggers_reinstall_even_when_installed(self, tmp_path):
        """browser-use present but unusable must not read as ready."""
        mgr = _installed_manager(tmp_path)

        def fake_run(argv, **kwargs):
            cmd = " ".join(str(a) for a in argv)
            if "import browser_use" in cmd:
                return _completed(json.dumps({"version": "1.0.0"}))
            # Only the metadata probe reports absent; the pip line must succeed.
            if "importlib.metadata" in cmd and "litellm" in cmd:
                return _completed("PackageNotFoundError", code=1)
            if "freeze" in cmd:
                return _completed("")
            return _completed("ok")

        with patch.object(subprocess, "run", side_effect=fake_run):
            status = mgr.ensure_installed()

        assert status.installed is True
        assert status.log, "a missing litellm must cause an install, not a silent early return"


class TestCompletionReporting:
    """`ok` means the subprocess answered; it does not mean the task got done."""

    def test_runner_reports_completed_and_errors(self, monkeypatch, capsys):
        import alpha.community.browser_use.runner as runner_mod

        class FakeResult:
            def final_result(self):
                return None

            def number_of_steps(self):
                return 8

            def is_done(self):
                return False

            def errors(self):
                return [None, "litellm.APIError: no credits"]

        class FakeAgent:
            def __init__(self, task, llm, use_vision=True):
                self.history = SimpleNamespace(urls=lambda: [], action_names=lambda: [], history=[])

            async def run(self, max_steps=500):
                return FakeResult()

        monkeypatch.setitem(sys.modules, "browser_use", SimpleNamespace(Agent=FakeAgent))
        monkeypatch.setattr(runner_mod, "_try_native_llm", lambda spec, model: SimpleNamespace())

        envelope, _code = TestRunnerScript._run_runner(monkeypatch, {"task": "t", "llm": {"model_name": "m"}}, capsys)
        assert envelope["ok"] is True
        assert envelope["completed"] is False
        assert envelope["errors"] == ["litellm.APIError: no credits"]

    def test_unfinished_run_is_not_reported_as_completed(self):
        envelope = {"ok": True, "completed": False, "errors": ["boom"], "result": "", "steps": 4, "history": []}
        text = tools_mod._render_envelope(envelope, version="0.13.11", model=None)
        assert "did NOT finish" in text
        assert "boom" in text
        assert "completed." not in text

    def test_finished_run_is_reported_as_completed(self):
        envelope = {"ok": True, "completed": True, "errors": [], "result": "Example Domain", "steps": 6, "history": [{"url": "https://example.com"}]}
        text = tools_mod._render_envelope(envelope, version="0.13.11", model=None)
        assert "completed." in text
        assert "Example Domain" in text
        assert "https://example.com" in text

    def test_provider_quota_error_is_surfaced_verbatim(self):
        """A credits/402 error is the single most actionable thing to report."""
        envelope = {
            "ok": True,
            "completed": False,
            "errors": ["litellm.APIError: OpenrouterException - {code: 402, requires more credits}"],
            "result": "",
            "steps": 8,
            "history": [],
        }
        text = tools_mod._render_envelope(envelope, version="0.13.11", model=None)
        assert "402" in text
        assert "did NOT finish" in text


class TestScreenshotEvidence:
    """Screenshots are the only visual proof; they must never be silently absent."""

    def _result_with_screenshots(self, b64_list):
        return SimpleNamespace(screenshots=lambda: list(b64_list), number_of_steps=lambda: len(b64_list), is_done=lambda: True, errors=lambda: [])

    def test_valid_pngs_are_written_to_disk(self, tmp_path):
        import base64
        import struct
        import zlib

        def tiny_png(color):
            raw = b"".join(b"\x00" + bytes(color * 4) for _ in range(2))

            def chunk(tag, data):
                c = tag + data
                return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

            return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

        from alpha.community.browser_use import runner as runner_mod

        encoded = [base64.b64encode(tiny_png(c)).decode() for c in ([255, 0, 0], [0, 255, 0])]
        paths = runner_mod._screenshot_paths(self._result_with_screenshots(encoded), str(tmp_path))
        assert len(paths) == 2
        for path, color in zip(paths, ([255, 0, 0], [0, 255, 0])):
            data = Path(path).read_bytes()
            assert data.startswith(b"\x89PNG")
            assert Path(path).parent.name == "screenshots"
            assert color[0] in data or color[1] in data

    def test_empty_list_is_empty_not_an_error(self, tmp_path):
        from alpha.community.browser_use import runner as runner_mod

        assert runner_mod._screenshot_paths(self._result_with_screenshots([]), str(tmp_path)) == []

    def test_non_png_payload_is_skipped_not_written(self, tmp_path):
        from alpha.community.browser_use import runner as runner_mod

        result = SimpleNamespace(screenshots=lambda: ["not-a-png"], number_of_steps=lambda: 1, is_done=lambda: True, errors=lambda: [])
        assert runner_mod._screenshot_paths(result, str(tmp_path)) == []
        assert not (tmp_path / "screenshots").exists() or not any((tmp_path / "screenshots").iterdir())

    def test_one_bad_frame_does_not_lose_the_rest(self, tmp_path):
        import base64
        import struct
        import zlib

        from alpha.community.browser_use import runner as runner_mod

        raw = b"\x00" + bytes([9] * 8)

        def chunk(tag, data):
            c = tag + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

        good = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
        mixed = ["garbage", base64.b64encode(good).decode()]
        paths = runner_mod._screenshot_paths(self._result_with_screenshots(mixed), str(tmp_path))
        assert len(paths) == 1

    def test_envelope_carries_screenshot_paths(self, monkeypatch, capsys):
        import base64
        import os
        import struct
        import tempfile
        import zlib

        raw = b"\x00" + bytes([7] * 8)

        def chunk(tag, data):
            c = tag + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

        class FakeResult:
            def final_result(self):
                return "done"

            def number_of_steps(self):
                return 1

            def is_done(self):
                return True

            def errors(self):
                return []

            def screenshots(self):
                return [base64.b64encode(png).decode()]

        class FakeAgent:
            def __init__(self, task, llm, use_vision=True):
                self.history = SimpleNamespace(urls=lambda: [], action_names=lambda: [], history=[])

            async def run(self, max_steps=500):
                return FakeResult()

        monkeypatch.setitem(sys.modules, "browser_use", SimpleNamespace(Agent=FakeAgent))
        from alpha.community.browser_use import runner as runner_mod

        monkeypatch.setattr(runner_mod, "_try_native_llm", lambda spec, model: SimpleNamespace())

        with tempfile.TemporaryDirectory() as scratch:
            previous = os.getcwd()
            os.chdir(scratch)
            try:
                envelope, _code = TestRunnerScript._run_runner(monkeypatch, {"task": "t", "llm": {"model_name": "m"}}, capsys)
                # Read the file while the scratch dir still exists; the path must
                # be absolute so it survives the chdir back.
                captured = [(path, Path(path).read_bytes()) for path in envelope["screenshots"]]
                absolute = all(Path(p).is_absolute() for p in envelope["screenshots"])
            finally:
                os.chdir(previous)

        assert len(captured) == 1
        assert absolute
        assert captured[0][1].startswith(b"\x89PNG"), "the screenshot must be a real PNG on disk"


class TestModelPreflight:
    """The shipped default model cannot drive browser-use; that must fail fast.

    `alpha-free` uses `alpha.models.free_router:ChatFreeLLM`, an Alpha-internal
    class the managed venv can never import. Discovering that inside the
    subprocess made the harness reinstall browser-use on every run and finally
    time out, reporting an install problem when the install was fine.
    """

    def test_internal_model_class_is_refused_with_guidance(self):
        with patch.object(manager_mod, "get_app_config", return_value=_config_with([_model_entry("alpha-free", use="alpha.models.free_router:ChatFreeLLM", model="auto")])):
            with pytest.raises(BrowserUseError, match="provider-backed"):
                tools_mod._resolve_and_check_spec(None)

    def test_provider_backed_model_is_accepted(self):
        with patch.object(manager_mod, "get_app_config", return_value=_config_with([_model_entry("union-alpha", use="langchain_openai:ChatOpenAI", model="unbiased/pareto")])):
            spec = tools_mod._resolve_and_check_spec("union-alpha")
        assert spec["model_name"] == "unbiased/pareto"

    def test_tool_refuses_before_any_install(self):
        """No install attempt may precede the model check."""
        mgr = MagicMock()
        cfg = {"default_model": "alpha-free"}
        with (
            patch.object(tools_mod, "get_browser_use_manager", return_value=mgr),
            patch.object(tools_mod, "_get_tool_config", return_value=cfg),
            patch.object(manager_mod, "get_app_config", return_value=_config_with([_model_entry("alpha-free", use="alpha.models.free_router:ChatFreeLLM", model="auto")])),
        ):
            result = asyncio.run(tools_mod.browser_use_run_tool.coroutine(runtime=SimpleNamespace(context={"thread_id": "t"}, state={"thread_data": {}}), task="do it", tool_call_id="c1"))
        assert "no usable model" in _message(result)
        mgr.ensure_installed.assert_not_called()
        mgr.run.assert_not_called()


class TestScreenshotPublishing:
    """Frames must reach the thread outputs, or the user has nothing to open.

    browser-use writes its frames into the managed venv's scratch dir, which the
    user cannot reach. Publishing them into the thread's outputs is what turns an
    internal artifact into something that opens in the artifacts panel and renders
    inline in the chat.
    """

    @staticmethod
    def _fake_png(path: Path, seed: int = 7) -> Path:
        import struct
        import zlib

        raw = b"\x00" + bytes([seed] * 8)

        def chunk(tag, data):
            c = tag + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        return path

    def _runtime_with_outputs(self, outputs: Path):
        return SimpleNamespace(context={"thread_id": "t"}, state={"thread_data": {"outputs_path": str(outputs)}})

    def test_frames_are_copied_into_thread_outputs_with_virtual_paths(self, tmp_path):
        source_dir = tmp_path / "scratch" / "screenshots"
        first = self._fake_png(source_dir / "step-000.png", 1)
        second = self._fake_png(source_dir / "step-001.png", 2)
        outputs = tmp_path / "outputs"

        published = tools_mod._publish_screenshots(self._runtime_with_outputs(outputs), {"screenshots": [str(first), str(second)]})

        assert len(published) == 2
        for item in published:
            assert item["virtual"].startswith("/mnt/user-data/outputs/browser-use/")
            assert Path(item["path"]).is_file()
            assert Path(item["path"]).read_bytes().startswith(b"\x89PNG")
            assert Path(item["path"]).parent.name == "browser-use"

        assert "step-001.png" in published[-1]["virtual"], "the newest frame must be published last"

    def test_limit_keeps_only_the_newest_frames(self, tmp_path):
        source_dir = tmp_path / "scratch"
        paths = [self._fake_png(source_dir / f"step-{index:03d}.png", index) for index in range(6)]
        outputs = tmp_path / "outputs"

        published = tools_mod._publish_screenshots(self._runtime_with_outputs(outputs), {"screenshots": [str(p) for p in paths]}, limit=2)

        assert len(published) == 2
        assert published[-1]["virtual"].endswith("step-005.png")

    def test_no_screenshots_publishes_nothing(self, tmp_path):
        assert tools_mod._publish_screenshots(self._runtime_with_outputs(tmp_path), {"screenshots": []}) == []
        assert tools_mod._publish_screenshots(self._runtime_with_outputs(tmp_path), {}) == []

    def test_missing_source_is_skipped_not_fatal(self, tmp_path):
        real = self._fake_png(tmp_path / "scratch" / "real.png", 3)
        outputs = tmp_path / "outputs"
        published = tools_mod._publish_screenshots(
            self._runtime_with_outputs(outputs),
            {"screenshots": [str(tmp_path / "scratch" / "gone.png"), str(real)]},
        )
        assert len(published) == 1

    def test_unavailable_outputs_path_is_not_fatal(self, tmp_path):
        """A warm-empty outputs dir must not cost the caller its browse result."""
        source = self._fake_png(tmp_path / "scratch" / "a.png", 4)
        runtime_missing_state = SimpleNamespace(context={"thread_id": "t"}, state=None)
        assert tools_mod._publish_screenshots(runtime_missing_state, {"screenshots": [str(source)]}) == []

        runtime_no_path = SimpleNamespace(context={"thread_id": "t"}, state={"thread_data": {}})
        assert tools_mod._publish_screenshots(runtime_no_path, {"screenshots": [str(source)]}) == []

    @pytest.mark.asyncio
    async def test_tool_attaches_artifacts_and_inline_view(self, tmp_path):
        mgr = MagicMock()
        status = manager_mod.BrowserUseStatus(installed=True, version="1.4.0", venv_path="/v", python_path="/v/python")
        mgr.status.return_value = status
        source = self._fake_png(tmp_path / "scratch" / "step-000.png", 5)
        mgr.run.return_value = {"ok": True, "completed": True, "errors": [], "result": "Example Domain", "steps": 1, "history": [{"url": "https://example.com"}], "screenshots": [str(source)]}

        outputs = tmp_path / "outputs"
        runtime = self._runtime_with_outputs(outputs)
        with (
            patch.object(tools_mod, "get_browser_use_manager", return_value=mgr),
            patch.object(tools_mod, "_get_tool_config", return_value={}),
            patch.object(tools_mod, "_resolve_and_check_spec", return_value={"use": "x:Y", "model_name": "m"}),
        ):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=runtime, task="do it", tool_call_id="c1")

        update = result.update
        # The artifact path is what the UI opens.
        assert update["artifacts"] and update["artifacts"][0].endswith("step-000.png")
        # The inline thumbnail key is the same one the stateful browser_* tools use.
        message = update["messages"][0]
        assert message.additional_kwargs["browser_view"]["screenshot"].endswith("step-000.png")
        assert "openable in this thread" in message.content

    @pytest.mark.asyncio
    async def test_failure_still_returns_a_message(self, tmp_path):
        """A publish failure must not swallow the browse result."""
        mgr = MagicMock()
        mgr.status.return_value = manager_mod.BrowserUseStatus(installed=True, version="1.4.0", venv_path="/v", python_path="/v/python")
        mgr.run.return_value = {"ok": False, "error": "boom", "steps": None, "history": []}
        with (
            patch.object(tools_mod, "get_browser_use_manager", return_value=mgr),
            patch.object(tools_mod, "_get_tool_config", return_value={}),
            patch.object(tools_mod, "_resolve_and_check_spec", return_value={}),
        ):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=self._runtime_with_outputs(tmp_path), task="do it", tool_call_id="c1")
        assert "boom" in _message(result)


class TestJudgeVerification:
    """browser-use's judge can disagree with the agent — and must be reported.

    A real run claimed example.com had no <h1> while plainly having one. The
    judge caught it. Reporting only the agent's own claim would have shipped that
    wrong answer as success.
    """

    def _fake_browser_use(self, monkeypatch, *, judgement):
        class FakeHistory:
            def urls(self):
                return ["https://example.com"]

            def action_names(self):
                return ["goto"]

        class FakeResult:
            def final_result(self):
                return "The page has no h1 heading."

            def number_of_steps(self):
                return 3

            def is_done(self):
                return True

            def errors(self):
                return []

            def judgement(self):
                return judgement

            def screenshots(self):
                return []

        class FakeAgent:
            def __init__(self, task, llm, use_vision=True, headless=True, use_judge=False, judge_llm=None, ground_truth=None):
                self.use_judge = use_judge
                self.ground_truth = ground_truth

            async def run(self, max_steps=500):
                return FakeResult()

        monkeypatch.setitem(sys.modules, "browser_use", SimpleNamespace(Agent=FakeAgent))
        return FakeAgent

    @pytest.mark.asyncio
    async def test_judge_failure_is_reported_not_hidden(self, monkeypatch):
        import alpha.community.browser_use.runner as runner_mod

        self._fake_browser_use(monkeypatch, judgement={"verdict": False, "failure_reason": "example.com does have an <h1> with 'Example Domain'", "reasoning": "agent claimed no h1"})
        monkeypatch.setattr(runner_mod, "_try_native_llm", lambda spec, model: SimpleNamespace())

        envelope = await runner_mod._run_agent({"task": "report the heading", "llm": {"model_name": "m"}, "verify": True, "ground_truth": {"heading": "Example Domain"}})

        assert envelope["judgement"]["verdict"] is False
        text = tools_mod._render_envelope(envelope, version="0.13.11", model=None)
        assert "Independent check (judge): FAIL" in text
        assert "Example Domain" in text
        assert "completed." in text, "the run itself did finish; only the answer is wrong"

    @pytest.mark.asyncio
    async def test_judge_pass_is_reported(self, monkeypatch):
        import alpha.community.browser_use.runner as runner_mod

        self._fake_browser_use(monkeypatch, judgement={"verdict": True, "reasoning": "heading matches", "failure_reason": ""})
        monkeypatch.setattr(runner_mod, "_try_native_llm", lambda spec, model: SimpleNamespace())

        envelope = await runner_mod._run_agent({"task": "t", "llm": {"model_name": "m"}, "verify": True})
        text = tools_mod._render_envelope(envelope, version="0.13.11", model=None)
        assert "Independent check (judge): PASS" in text

    @pytest.mark.asyncio
    async def test_no_verify_means_no_judge_and_no_verdict_line(self, monkeypatch):
        import alpha.community.browser_use.runner as runner_mod

        self._fake_browser_use(monkeypatch, judgement=None)
        monkeypatch.setattr(runner_mod, "_try_native_llm", lambda spec, model: SimpleNamespace())

        envelope = await runner_mod._run_agent({"task": "t", "llm": {"model_name": "m"}})
        assert envelope["judgement"] is None
        assert "Independent check" not in tools_mod._render_envelope(envelope, version="0.13.11", model=None)

    def test_verify_flags_reach_the_agent(self, monkeypatch):
        """The operator's verify/ground_truth must actually reach browser-use."""
        seen = {}

        class FakeAgent:
            def __init__(self, task, llm, use_vision=True, headless=True, use_judge=False, judge_llm=None, ground_truth=None):
                seen["use_judge"] = use_judge
                seen["ground_truth"] = ground_truth

            async def run(self, max_steps=500):
                raise AssertionError("should not run")

        monkeypatch.setitem(sys.modules, "browser_use", SimpleNamespace(Agent=FakeAgent))
        import alpha.community.browser_use.runner as runner_mod

        monkeypatch.setattr(runner_mod, "_try_native_llm", lambda spec, model: SimpleNamespace())
        payload = {"task": "t", "llm": {"model_name": "m"}, "verify": True, "ground_truth": {"heading": "Example Domain"}}
        try:
            asyncio.run(runner_mod._run_agent(payload))
        except AssertionError:
            pass
        assert seen["use_judge"] is True
        assert seen["ground_truth"] == {"heading": "Example Domain"}

    @pytest.mark.asyncio
    async def test_verify_without_ground_truth_is_refused_before_launching(self):
        """browser-use types ground_truth as a string; a dict failed inside the
        subprocess *after* the browser launched, so the shape is checked up front."""
        mgr = MagicMock()
        with (
            patch.object(tools_mod, "get_browser_use_manager", return_value=mgr),
            patch.object(tools_mod, "_get_tool_config", return_value={}),
            patch.object(tools_mod, "_resolve_and_check_spec", return_value={}),
        ):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="do it", tool_call_id="c1", verify=True)
        assert "ground_truth" in _message(result)
        mgr.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_dict_ground_truth_is_refused_with_guidance(self):
        """The exact mistake a caller makes: passing a mapping."""
        mgr = MagicMock()
        with (
            patch.object(tools_mod, "get_browser_use_manager", return_value=mgr),
            patch.object(tools_mod, "_get_tool_config", return_value={}),
            patch.object(tools_mod, "_resolve_and_check_spec", return_value={}),
        ):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="do it", tool_call_id="c1", verify=True, ground_truth={"page_title": "Example Domain"})
        assert "plain sentence" in _message(result)
        mgr.run.assert_not_called()

    def test_ground_truth_is_declared_as_a_string(self):
        """browser-use's AgentSettings types this as a string."""
        import inspect

        annotation = inspect.signature(tools_mod.browser_use_run_tool.coroutine).parameters["ground_truth"].annotation
        assert str(annotation) == "str | None"

    @pytest.mark.asyncio
    async def test_verify_and_ground_truth_reach_the_run(self):
        mgr = MagicMock()
        mgr.status.return_value = manager_mod.BrowserUseStatus(installed=True, version="1.4.0", venv_path="/v", python_path="/v/python")
        mgr.run.return_value = {"ok": True, "completed": True, "errors": [], "result": "done", "steps": 1, "history": [], "judgement": {"verdict": True, "reasoning": "matches"}}
        with (
            patch.object(tools_mod, "get_browser_use_manager", return_value=mgr),
            patch.object(tools_mod, "_get_tool_config", return_value={}),
            patch.object(tools_mod, "_resolve_and_check_spec", return_value={}),
        ):
            result = await tools_mod.browser_use_run_tool.coroutine(runtime=_runtime(), task="do it", tool_call_id="c1", verify=True, ground_truth="the heading is Example Domain")
        assert mgr.run.call_args.kwargs["verify"] is True
        assert mgr.run.call_args.kwargs["ground_truth"] == "the heading is Example Domain"
        assert "judge): PASS" in _message(result)

    def test_judge_llm_falls_back_to_the_driving_model(self, monkeypatch):
        """A separate judge is a second opinion on the same evidence, not a different model."""
        import alpha.community.browser_use.runner as runner_mod

        seen = {}

        def fake_build(spec):
            seen["specs"] = seen.get("specs", 0) + 1
            return SimpleNamespace(tag=f"llm{seen['specs']}")

        class FakeAgent:
            def __init__(self, task, llm, use_vision=True, headless=True, use_judge=False, judge_llm=None, ground_truth=None):
                seen["judge_llm"] = judge_llm

            async def run(self, max_steps=500):
                raise AssertionError("should not run")

        monkeypatch.setitem(sys.modules, "browser_use", SimpleNamespace(Agent=FakeAgent))
        monkeypatch.setattr(runner_mod, "_build_llm", fake_build)
        try:
            asyncio.run(runner_mod._run_agent({"task": "t", "llm": {"model_name": "m"}, "verify": True}))
        except AssertionError:
            pass
        assert seen["judge_llm"].tag == "llm2", "judge must reuse the driving model spec"


class TestBrowserStartRetry:
    """browser-use's own 30s browser-launch timeout is worth exactly one retry.

    It fired on a loaded host with vision enabled and killed whole runs over
    nothing to do with the task. Everything else — including our own wall-clock
    timeout — must never be retried behind the caller's back.
    """

    LAUNCH_ERROR = "TimeoutError: Event handler browser_use.browser.watchdog_base.BrowserSession.on_BrowserStartEvent#4288 timed out after 30.0s"

    def test_launch_timeout_is_recognised(self):
        assert manager_mod._is_browser_start_timeout({"error": self.LAUNCH_ERROR})

    def test_our_own_timeout_is_never_retried(self):
        """Retrying a task we already killed would double its cost for nothing."""
        assert manager_mod._is_browser_start_timeout({"error": "browser-use ran past its budget", "timed_out": True}) is False

    def test_real_task_failures_are_not_retried(self):
        for error in ("login required", "page not found", "Invalid model output format", ""):
            assert manager_mod._is_browser_start_timeout({"error": error}) is False

    def test_run_retries_once_then_reports(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        calls = []

        def fake_run_once(**kwargs):
            calls.append(kwargs)
            return {"ok": False, "error": self.LAUNCH_ERROR, "timed_out": False}

        with patch.object(mgr, "_run_once", side_effect=fake_run_once), patch.object(manager_mod.time, "sleep"):
            envelope = mgr.run(task="t", llm_spec={})

        assert len(calls) == 2, "one re-attempt, then give up"
        assert envelope["retried_after_browser_start_timeout"] == 1

    def test_run_does_not_retry_a_successful_run(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        calls = []

        def fake_run_once(**kwargs):
            calls.append(kwargs)
            return {"ok": True, "completed": True, "result": "done", "errors": []}

        with patch.object(mgr, "_run_once", side_effect=fake_run_once):
            envelope = mgr.run(task="t", llm_spec={})

        assert len(calls) == 1
        assert envelope["result"] == "done"
        assert "retried_after_browser_start_timeout" not in envelope

    def test_run_does_not_retry_a_task_failure(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        calls = []

        def fake_run_once(**kwargs):
            calls.append(kwargs)
            return {"ok": True, "completed": False, "errors": ["login required"], "result": ""}

        with patch.object(mgr, "_run_once", side_effect=fake_run_once):
            envelope = mgr.run(task="t", llm_spec={})

        assert len(calls) == 1, "a genuine task failure must be reported, not retried"
        assert envelope["errors"] == ["login required"]

    def test_retry_succeeds_and_returns_cleanly(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        outcomes = [{"ok": False, "error": self.LAUNCH_ERROR, "timed_out": False}, {"ok": True, "completed": True, "result": "done", "errors": []}]

        with patch.object(mgr, "_run_once", side_effect=outcomes), patch.object(manager_mod.time, "sleep"):
            envelope = mgr.run(task="t", llm_spec={})

        assert envelope["result"] == "done"
        assert "retried_after_browser_start_timeout" not in envelope


class TestJudgeKeyShape:
    """The judge's real key is `verdict`; reading `passed` hides every verdict.

    A real 0.13.11 run returned
    ``{'verdict': True, 'reasoning': ..., 'failure_reason': '', 'impossible_task': False, 'reached_captcha': False}``.
    Reading only `passed` rendered that genuine PASS as "inconclusive" — a
    verification feature that silently reports nothing is worse than no feature.
    """

    def test_real_passing_judgement_renders_as_pass(self):
        envelope = {
            "ok": True,
            "completed": True,
            "errors": [],
            "result": "Example Domain",
            "steps": 4,
            "history": [],
            "judgement": {"verdict": True, "reasoning": "the heading matches", "failure_reason": "", "impossible_task": False, "reached_captcha": False},
        }
        text = tools_mod._render_envelope(envelope, version="0.13.11", model=None)
        assert "judge): PASS" in text
        assert "inconclusive" not in text
        assert "the heading matches" in text

    def test_real_failing_judgement_renders_as_fail_with_the_reason(self):
        envelope = {
            "ok": True,
            "completed": True,
            "errors": [],
            "result": "there is no heading",
            "steps": 3,
            "history": [],
            "judgement": {"verdict": False, "reasoning": "agent summarised its own step", "failure_reason": "example.com does have an <h1>", "impossible_task": False, "reached_captcha": False},
        }
        text = tools_mod._render_envelope(envelope, version="0.13.11", model=None)
        assert "judge): FAIL" in text
        assert "example.com does have an <h1>" in text
        assert "do not rely on it" in text

    def test_impossible_and_captcha_are_explained(self):
        for flag in ("impossible_task", "reached_captcha"):
            envelope = {"ok": True, "completed": True, "errors": [], "result": "x", "judgement": {"verdict": False, "failure_reason": "n/a", flag: True}}
            text = tools_mod._render_envelope(envelope, version="0.13.11", model=None)
            assert ("impossible as phrased" in text) or ("CAPTCHA" in text)

    def test_passed_key_still_works_for_other_versions(self):
        envelope = {"ok": True, "completed": True, "errors": [], "result": "x", "judgement": {"passed": True, "reason": "ok"}}
        assert "judge): PASS" in tools_mod._render_envelope(envelope, version="0.13.11", model=None)


class TestConfigWiring:
    """The `use:` paths in config.example.yaml only fail at operator runtime.

    A mistyped dotted path in a commented-out example block is invisible until
    someone uncomments it on their machine, so the resolution is pinned here.
    """

    @pytest.mark.parametrize(
        "target",
        [
            "alpha.community.browser_use.tools:browser_use_setup_tool",
            "alpha.community.browser_use.tools:browser_use_run_tool",
        ],
    )
    def test_use_path_resolves_to_the_expected_tool(self, target):
        import importlib

        module_path, _, symbol = target.partition(":")
        resolved = getattr(importlib.import_module(module_path), symbol)
        assert resolved is tools_mod.browser_use_setup_tool or resolved is tools_mod.browser_use_run_tool

    def test_example_config_mentions_both_tools(self):
        example = Path(__file__).resolve().parents[2] / "config.example.yaml"
        text = example.read_text(encoding="utf-8")
        assert "alpha.community.browser_use.tools:browser_use_setup_tool" in text
        assert "alpha.community.browser_use.tools:browser_use_run_tool" in text


class TestToolSchema:
    def test_runtime_is_the_first_parameter(self):
        import inspect

        # Async ``@tool`` objects expose the underlying function on
        # ``.coroutine`` (``.func`` stays None), so this is where the injected
        # runtime argument has to be declared.
        for tool_obj in (tools_mod.browser_use_setup_tool, tools_mod.browser_use_run_tool):
            params = list(inspect.signature(tool_obj.coroutine).parameters)
            assert params[0] == "runtime"

    def test_runtime_is_required_not_defaulted(self):
        """``Runtime | None = None`` silently breaks injected-argument detection."""
        import inspect

        for tool_obj in (tools_mod.browser_use_setup_tool, tools_mod.browser_use_run_tool):
            assert inspect.signature(tool_obj.coroutine).parameters["runtime"].default is inspect.Parameter.empty

    def test_tool_names_match_config_entries(self):
        assert tools_mod.browser_use_setup_tool.name == "browser_use_setup"
        assert tools_mod.browser_use_run_tool.name == "browser_use_run"


class TestRunnerScript:
    """The child side, exercised against a fake browser_use module.

    The fakes imitate the **real** 0.13.x shapes found by installing for real:
    ``Agent`` has no ``headless`` parameter, ``run()`` returns an
    ``AgentHistoryList`` (not a string), and ``final_result()`` is ``None`` for a
    run that exhausted its budget. Modelling the earlier shapes here would let
    the child protocol pass while every real run failed.
    """

    @staticmethod
    def _fake_browser_use(monkeypatch, *, fail: bool = False, finished: bool = True, urls=None, actions=None):
        module = SimpleNamespace()

        class FakeHistory:
            def __init__(self):
                self.history = []

            def urls(self):
                return list(urls or [])

            def action_names(self):
                return list(actions or [])

            def is_done(self):
                return finished

            def errors(self):
                return ["No module named 'litellm'"] if fail else []

        class FakeAgent:
            def __init__(self, task, llm, use_vision=True):
                self.task = task
                self.llm = llm
                self.use_vision = use_vision
                self.history = FakeHistory()

            async def run(self, max_steps=500):
                if fail:
                    raise RuntimeError("page never loaded")
                self.history = SimpleNamespace(
                    urls=lambda: list(urls or []),
                    action_names=lambda: list(actions or []),
                    history=[],
                )
                return SimpleNamespace(
                    final_result=lambda: "the answer" if finished else None,
                    number_of_steps=lambda: 7,
                    is_done=lambda: finished,
                    errors=lambda: ["No module named 'litellm'"] if fail else [],
                )

        module.Agent = FakeAgent
        monkeypatch.setitem(sys.modules, "browser_use", module)
        return FakeAgent

    @staticmethod
    def _no_native_llm(monkeypatch):
        """Make browser-use's own ChatLiteLLM adapter unavailable."""
        import alpha.community.browser_use.runner as runner_mod

        monkeypatch.setattr(runner_mod, "_try_native_llm", lambda spec, model: None)

    @staticmethod
    def _run_runner(monkeypatch, payload: dict, capsys) -> tuple[dict, int]:
        from alpha.community.browser_use import runner

        monkeypatch.setattr(sys, "stdin", SimpleNamespace(read=lambda: json.dumps(payload)))
        code = runner.main()
        out = capsys.readouterr().out.strip().splitlines()
        return json.loads(out[-1]), code

    def test_emits_a_result_envelope(self, monkeypatch, capsys):
        self._fake_browser_use(monkeypatch, urls=["https://a.test"], actions=["navigate"])
        self._no_native_llm(monkeypatch)
        monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=lambda **kw: SimpleNamespace(**kw)))
        envelope, code = self._run_runner(monkeypatch, {"task": "check", "llm": {"use": "langchain_openai:ChatOpenAI", "model_name": "m"}, "max_steps": 4}, capsys)
        assert envelope["ok"] is True
        assert envelope["result"] == "the answer"
        assert envelope["completed"] is True
        assert envelope["errors"] == []
        assert envelope["steps"] == 7
        assert envelope["history"][0]["url"] == "https://a.test"
        assert code == 0

    def test_unfinished_run_never_reports_an_internal_repr(self, monkeypatch, capsys):
        """`str(AgentHistoryList)` is a pydantic dump, not an answer."""
        self._fake_browser_use(monkeypatch, finished=False, urls=["https://a.test"], actions=["navigate"])
        self._no_native_llm(monkeypatch)
        monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=lambda **kw: SimpleNamespace(**kw)))
        envelope, _code = self._run_runner(monkeypatch, {"task": "check", "llm": {"use": "langchain_openai:ChatOpenAI", "model_name": "m"}}, capsys)
        assert "AgentHistoryList" not in envelope["result"]
        assert "No final answer" in envelope["result"]
        assert envelope["completed"] is False

    def test_native_browser_use_llm_adapter_is_preferred(self, monkeypatch, capsys):
        """0.13.x rejects a bare ChatOpenAI (no `.provider`), so the native one wins."""
        captured = {}

        class FakeChatLiteLLM:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        self._fake_browser_use(monkeypatch)
        monkeypatch.setitem(sys.modules, "browser_use.llm.litellm.chat", SimpleNamespace(ChatLiteLLM=FakeChatLiteLLM))

        envelope, _code = self._run_runner(
            monkeypatch,
            {"task": "check", "llm": {"use": "langchain_openai:ChatOpenAI", "model_name": "m", "api_key": "k", "base_url": "https://gw.test"}},
            capsys,
        )
        assert envelope["ok"] is True
        # `m` on an unknown host routes through LiteLLM's openai-compatible
        # provider, so the id arrives provider-qualified.
        assert captured == {"model": "openai/m", "api_key": "k", "api_base": "https://gw.test"}

    def test_base_url_maps_to_api_base_on_the_native_adapter(self, monkeypatch):
        from alpha.community.browser_use import runner as runner_mod

        captured = {}

        class FakeChatLiteLLM:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setitem(sys.modules, "browser_use.llm.litellm.chat", SimpleNamespace(ChatLiteLLM=FakeChatLiteLLM))
        llm = runner_mod._try_native_llm({"api_key": "k", "base_url": "https://gw.test", "extra": {}}, "m")

        assert llm is not None
        assert captured == {"model": "openai/m", "api_key": "k", "api_base": "https://gw.test"}

    def test_native_adapter_absent_falls_back_to_the_declared_class(self, monkeypatch):
        """Older browser-use builds have no ChatLiteLLM; the declared class must win."""
        from alpha.community.browser_use import runner as runner_mod

        monkeypatch.setitem(sys.modules, "browser_use.llm.litellm.chat", None)  # forces ImportError
        monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=lambda **kw: SimpleNamespace(**kw)))
        llm = runner_mod._build_llm({"use": "langchain_openai:ChatOpenAI", "model_name": "m", "api_key": "k"})
        assert llm.model == "m"

    def test_missing_model_name_is_refused(self):
        from alpha.community.browser_use import runner as runner_mod

        with pytest.raises(ValueError, match="no model_name"):
            runner_mod._build_llm({"use": "langchain_openai:ChatOpenAI"})

    def test_start_url_is_folded_into_the_task(self, monkeypatch, capsys):
        agent_cls = self._fake_browser_use(monkeypatch)
        self._no_native_llm(monkeypatch)
        monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=lambda **kw: SimpleNamespace(**kw)))
        captured = {}
        original = agent_cls.__init__

        def spy(self, *a, **kw):
            captured["task"] = kw["task"]
            original(self, *a, **kw)

        agent_cls.__init__ = spy
        self._run_runner(monkeypatch, {"task": "check", "llm": {"use": "langchain_openai:ChatOpenAI", "model_name": "m"}, "start_url": "https://a.test"}, capsys)
        assert "https://a.test" in captured["task"]

    def test_agent_failure_becomes_data_not_a_crash(self, monkeypatch, capsys):
        self._fake_browser_use(monkeypatch, fail=True)
        self._no_native_llm(monkeypatch)
        monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=lambda **kw: SimpleNamespace(**kw)))
        envelope, code = self._run_runner(monkeypatch, {"task": "check", "llm": {"use": "langchain_openai:ChatOpenAI", "model_name": "m"}}, capsys)
        assert envelope["ok"] is False
        assert "page never loaded" in envelope["error"]
        assert code == 1

    def test_missing_llm_client_names_the_package_to_install(self, monkeypatch, capsys):
        self._fake_browser_use(monkeypatch)
        self._no_native_llm(monkeypatch)
        monkeypatch.setitem(sys.modules, "langchain_anthropic", None)  # forces ImportError
        envelope, _code = self._run_runner(monkeypatch, {"task": "check", "llm": {"use": "langchain_anthropic:ChatAnthropic", "model_name": "m"}}, capsys)
        assert envelope["ok"] is False
        assert "langchain-anthropic" in envelope["error"]

    def test_invalid_json_gets_an_envelope(self, monkeypatch, capsys):
        from alpha.community.browser_use import runner

        monkeypatch.setattr(sys, "stdin", SimpleNamespace(read=lambda: "not json"))
        code = runner.main()
        envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert envelope["ok"] is False
        assert code == 2


class TestUnicodeSafety:
    """Windows consoles are cp1252; a glyph in browser-use's output must not kill a run."""

    def test_envelope_is_ascii_only(self):
        """Pure-ASCII output survives any console encoding, so parsing always works."""
        from alpha.community.browser_use import runner

        runner._emit({"ok": True, "result": "navigated \u25b6 \U0001f517"})
        # _emit writes to the real stdout; assert the serialization is ASCII-safe
        # rather than the byte stream, which depends on the test capture.
        assert json.dumps({"ok": True, "result": "navigated \u25b6 \U0001f517"}, ensure_ascii=True).isascii()

    def test_streams_are_reconfigured_without_raising(self):
        from alpha.community.browser_use import runner

        # Idempotent and non-fatal even when streams are already replaced by pytest.
        runner._force_utf8_streams()
        runner._force_utf8_streams()

    def test_subprocess_uses_utf8_decoding(self, tmp_path):
        """The parent must not decode the child's UTF-8 with the console codec."""
        mgr = _installed_manager(tmp_path)
        proc = MagicMock()
        proc.communicate.return_value = ('{"ok": true, "result": "x", "steps": 1, "history": []}', "")
        with patch.object(mgr, "probe_version", return_value="1.4.0"), patch.object(subprocess, "Popen", return_value=proc) as popen:
            mgr.run(task="t", llm_spec={})
        kwargs = popen.call_args.kwargs
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs.get("errors") == "backslashreplace"

    def test_child_env_forces_utf8(self, tmp_path):
        mgr = _installed_manager(tmp_path)
        proc = MagicMock()
        proc.communicate.return_value = ('{"ok": true, "result": "x", "steps": 1, "history": []}', "")
        with patch.object(mgr, "probe_version", return_value="1.4.0"), patch.object(subprocess, "Popen", return_value=proc) as popen:
            mgr.run(task="t", llm_spec={})
        env = popen.call_args.kwargs["env"]
        assert env["PYTHONIOENCODING"].startswith("utf-8")
        assert env["PYTHONUTF8"] == "1"
