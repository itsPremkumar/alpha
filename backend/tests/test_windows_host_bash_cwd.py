"""Windows host bash anchors relative paths via the process, not a POSIX prefix.

On Windows the host-bash sandbox may resolve to Windows PowerShell 5.1, which
rejects ``&&`` ("The token '&&' is not a valid statement separator in this
version."), or to cmd.exe, where single-quoted paths are not quoting. The
``cd <workspace> && <command>`` prefix that local bash calls used to receive is
POSIX shell syntax, so **every** command failed at the parser before the
model's own syntax was even considered. No single prefix spelling works across
``sh`` / ``pwsh`` / Windows PowerShell 5.1 / ``cmd.exe``; the working directory
is therefore set on the subprocess itself, which every candidate shell honors
identically.
"""

from __future__ import annotations

from types import SimpleNamespace

import alpha.sandbox.local.local_sandbox as local_sandbox
from alpha.sandbox.local.local_sandbox import LocalSandbox, PathMapping
from alpha.sandbox.tools import bash_tool

# ---------------------------------------------------------------------------
# LocalSandbox: the mapped thread workspace becomes the process working dir
# ---------------------------------------------------------------------------

_POWERSHELL_5_1 = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def _capturing_windows_runner(captured: dict):
    def fake_run(args, timeout, env=None, cwd=None):
        captured["args"] = args
        captured["cwd"] = cwd
        return ("ok", "", 0, False)

    return fake_run


def test_execute_command_runs_in_mapped_workspace_on_windows(monkeypatch, tmp_path):
    captured: dict = {}
    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: _POWERSHELL_5_1))
    monkeypatch.setattr(LocalSandbox, "_run_windows_command", staticmethod(_capturing_windows_runner(captured)))

    sandbox = LocalSandbox(
        "local:u:t",
        [PathMapping(container_path="/mnt/user-data/workspace", local_path=str(tmp_path))],
    )
    output = sandbox.execute_command("echo hello")

    assert output == "ok"
    assert captured["cwd"] == str(tmp_path)
    # The command string itself stays untouched: no POSIX `cd ... &&` prefix may
    # reach a shell that cannot parse it.
    assert captured["args"][-1] == "echo hello"


def test_execute_command_without_workspace_mapping_keeps_inherited_cwd(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: "cmd.exe"))
    monkeypatch.setattr(LocalSandbox, "_run_windows_command", staticmethod(_capturing_windows_runner(captured)))

    LocalSandbox("local").execute_command("echo hello")

    assert captured["cwd"] is None


def test_execute_command_with_missing_workspace_dir_keeps_inherited_cwd(monkeypatch, tmp_path):
    captured: dict = {}
    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: "cmd.exe"))
    monkeypatch.setattr(LocalSandbox, "_run_windows_command", staticmethod(_capturing_windows_runner(captured)))

    sandbox = LocalSandbox(
        "local",
        [PathMapping(container_path="/mnt/user-data/workspace", local_path=str(tmp_path / "not-created"))],
    )
    sandbox.execute_command("echo hello")

    assert captured["cwd"] is None


# ---------------------------------------------------------------------------
# bash_tool: skip the POSIX `cd ... &&` prefix on Windows
# ---------------------------------------------------------------------------

_THREAD_DATA = {
    "workspace_path": "C:/my ws/t1",
    "uploads_path": "C:/my ws/t1/uploads",
    "outputs_path": "C:/my ws/t1/outputs",
}


class _CapturingSandbox:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute_command(self, command, env=None, timeout=None):
        self.commands.append(command)
        return "done"


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        state={"sandbox": {"sandbox_id": "local:u:t"}, "thread_data": dict(_THREAD_DATA)},
        context={"thread_id": "t1"},
    )


def _patch_local_bash(monkeypatch, sandbox: _CapturingSandbox, *, windows: bool) -> None:
    monkeypatch.setattr("alpha.sandbox.tools.ensure_sandbox_initialized", lambda runtime: sandbox)
    monkeypatch.setattr("alpha.sandbox.tools.is_local_sandbox", lambda runtime: True)
    monkeypatch.setattr("alpha.sandbox.tools.is_host_bash_allowed", lambda: True)
    monkeypatch.setattr("alpha.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)
    monkeypatch.setattr("alpha.sandbox.tools.validate_local_bash_command_paths", lambda command, thread_data: None)
    monkeypatch.setattr("alpha.sandbox.tools.replace_virtual_paths_in_command", lambda command, thread_data: command)
    monkeypatch.setattr("alpha.sandbox.tools._is_windows", lambda: windows)
    fake_cfg = SimpleNamespace(sandbox=SimpleNamespace(bash_output_max_chars=10000, bash_command_timeout=42))
    monkeypatch.setattr("alpha.config.app_config.get_app_config", lambda: fake_cfg)


def test_bash_tool_skips_posix_cd_prefix_on_windows(monkeypatch):
    sandbox = _CapturingSandbox()
    _patch_local_bash(monkeypatch, sandbox, windows=True)

    bash_tool.func(runtime=_runtime(), command="echo hi", description="test")

    # Windows anchors the workspace through the subprocess cwd instead; the
    # command handed to the shell must be the user's command unchanged.
    assert sandbox.commands == ["echo hi"]


def test_bash_tool_keeps_cd_prefix_on_posix(monkeypatch):
    sandbox = _CapturingSandbox()
    _patch_local_bash(monkeypatch, sandbox, windows=False)

    bash_tool.func(runtime=_runtime(), command="echo hi", description="test")

    assert sandbox.commands == ["cd 'C:/my ws/t1' && echo hi"]
