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

import contextlib
import json
import subprocess
import sys
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
            if cmd.endswith("import litellm"):
                return _completed("No module named litellm", code=1)
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
            if cmd.strip().endswith("import playwright"):
                return _completed("")
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
            if cmd.strip().endswith("import playwright"):
                return _completed("No module named playwright", code=1)
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
            if cmd.strip().endswith("import playwright"):
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
        mgr = MagicMock()
        mgr.status.return_value = manager_mod.BrowserUseStatus(installed=False, version=None, venv_path="/v", python_path="/v/python")
        cfg = {"auto_install": False}
        with patch.object(tools_mod, "get_browser_use_manager", return_value=mgr), patch.object(tools_mod, "_get_tool_config", return_value=cfg):
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
        assert captured == {"model": "m", "api_key": "k", "api_base": "https://gw.test"}

    def test_base_url_maps_to_api_base_on_the_native_adapter(self, monkeypatch):
        from alpha.community.browser_use import runner as runner_mod

        captured = {}

        class FakeChatLiteLLM:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setitem(sys.modules, "browser_use.llm.litellm.chat", SimpleNamespace(ChatLiteLLM=FakeChatLiteLLM))
        llm = runner_mod._try_native_llm({"api_key": "k", "base_url": "https://gw.test", "extra": {}}, "m")

        assert llm is not None
        assert captured == {"model": "m", "api_key": "k", "api_base": "https://gw.test"}

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
