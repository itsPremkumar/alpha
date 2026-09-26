"""Regression tests for exception/refusal -> empty/zero/success conversions in
the sandbox and exec tools.

Each test here pins a specific conversion that was found and fixed, and fails if
the conversion comes back.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from alpha.sandbox import tools as sandbox_tools
from alpha.sandbox.local.list_dir import list_dir
from alpha.sandbox.local.local_sandbox import LocalSandbox, resolve_command_exit_status
from alpha.sandbox.sandbox import Sandbox

# ---------------------------------------------------------------------------
# write_file(append=True): a failed pre-read used to skip the syntax guard and
# still report "OK"
# ---------------------------------------------------------------------------


def _runtime(root: Path) -> SimpleNamespace:
    for sub in ("workspace", "uploads", "outputs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local:t1"},
            "thread_data": {
                "workspace_path": str(root / "workspace"),
                "uploads_path": str(root / "uploads"),
                "outputs_path": str(root / "outputs"),
            },
        },
        context={"thread_id": "t1"},
    )


class _UnreadableSandbox:
    """A local sandbox whose pre-write read fails with a real, non-ENOENT error.

    This models the case that used to be swallowed: the append path could not
    read the current content, so ``validate_syntax_precommit`` never ran, and the
    tool still answered "OK" -- a write published as syntax-verified when no
    verification had happened at all.
    """

    def __init__(self, real: Sandbox) -> None:
        self._real = real

    def read_file(self, path, start_line=None, end_line=None):
        raise OSError("simulated I/O failure reading the current file")

    def __getattr__(self, item):
        return getattr(self._real, item)


def test_append_with_unreadable_current_content_is_not_reported_ok(tmp_path, monkeypatch):
    rt = _runtime(tmp_path / "thread")
    real = LocalSandbox("t1")
    monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime=None: _UnreadableSandbox(real))
    monkeypatch.setattr(sandbox_tools, "ensure_thread_directories_exist", lambda runtime=None: None)

    out = sandbox_tools.write_file_tool.func(
        runtime=rt,
        path="/mnt/user-data/workspace/append.py",
        content="x = 1\n",
        append=True,
    )
    assert out != "OK", f"append published as successful despite a failed pre-read: {out!r}"
    assert out.startswith("Error:"), out
    assert "could not be read" in out, out
    # Nothing was written.
    assert not (tmp_path / "thread" / "workspace" / "append.py").exists()


def test_append_to_a_new_file_still_works(tmp_path, monkeypatch):
    """A missing file is the one legitimate "nothing to prepend to" case."""
    rt = _runtime(tmp_path / "thread")
    monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime=None: LocalSandbox("t1"))
    monkeypatch.setattr(sandbox_tools, "ensure_thread_directories_exist", lambda runtime=None: None)

    target = tmp_path / "thread" / "workspace" / "new_append.py"
    first = sandbox_tools.write_file_tool.func(
        runtime=rt, path="/mnt/user-data/workspace/new_append.py", content="a = 1\n", append=True
    )
    assert first == "OK", first
    second = sandbox_tools.write_file_tool.func(
        runtime=rt, path="/mnt/user-data/workspace/new_append.py", content="b = 2\n", append=True
    )
    assert second == "OK", second
    assert target.read_text(encoding="utf-8") == "a = 1\nb = 2\n"


# ---------------------------------------------------------------------------
# list_dir: a PermissionError used to become [] and then render as "(empty)"
# ---------------------------------------------------------------------------


def test_unreadable_root_raises_instead_of_returning_empty(tmp_path, monkeypatch):
    locked = tmp_path / "locked_workspace"
    locked.mkdir()
    (locked / "visible.txt").write_text("hi", encoding="utf-8")

    real_iterdir = Path.iterdir

    def deny(self):
        if self.resolve() == locked.resolve():
            raise PermissionError(13, "Permission denied", str(self))
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", deny)

    # A refusal must not be flattened into an empty list.
    with pytest.raises(PermissionError):
        list_dir(str(locked))

    # ...and the tool surface must render it as a refusal, not "(empty)".
    rt = _runtime(tmp_path / "thread")
    (tmp_path / "thread" / "workspace" / "visible.txt").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime=None: LocalSandbox("t1"))
    monkeypatch.setattr(sandbox_tools, "ensure_thread_directories_exist", lambda runtime=None: None)

    ok_dir = sandbox_tools.ls_tool.func(runtime=rt, path="/mnt/user-data/workspace")
    assert "visible.txt" in ok_dir, ok_dir

    monkeypatch.undo()
    monkeypatch.setattr(Path, "iterdir", deny)
    # Point the thread's workspace at the directory that cannot be read.
    rt.state["thread_data"]["workspace_path"] = str(locked)
    denied = sandbox_tools.ls_tool.func(runtime=rt, path="/mnt/user-data/workspace")
    assert denied != "(empty)", f"a refused listing was published as empty: {denied!r}"
    assert "Permission denied" in denied, denied


def test_empty_directory_still_returns_empty(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    assert list_dir(str(root)) == []


# ---------------------------------------------------------------------------
# execute_command: an unreaped process used to be reported as exit code 0
# ---------------------------------------------------------------------------


def test_unknown_exit_status_is_never_zero():
    # Unknown must be a failure sentinel, never a fabricated success.
    code, timed_out = resolve_command_exit_status(None, timed_out=False)
    assert code != 0, "an unknown exit status must never be reported as success"
    assert code == 124
    assert timed_out is True


def test_known_exit_status_is_passed_through():
    assert resolve_command_exit_status(0, timed_out=False) == (0, False)
    assert resolve_command_exit_status(7, timed_out=False) == (7, False)
    assert resolve_command_exit_status(7, timed_out=True) == (7, True)


def test_both_command_runners_use_the_shared_exit_status_rule():
    """Pin the call sites, not just the helper.

    The defect was literally ``returncode if process.returncode is not None
    else 0`` in each runner, so a test that only exercises the helper would
    still pass with the fabrication back in place.
    """
    import ast

    from alpha.sandbox.local import local_sandbox

    tree = ast.parse(Path(local_sandbox.__file__).read_text(encoding="utf-8"))
    runners = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"_run_windows_command", "_run_posix_command"}
    }
    assert set(runners) == {"_run_windows_command", "_run_posix_command"}

    for name, func in runners.items():
        calls_shared = any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "resolve_command_exit_status"
            for node in ast.walk(func)
        )
        assert calls_shared, f"{name} must resolve its exit status through resolve_command_exit_status"

        # No conditional expression may map an unknown status onto 0.
        for node in ast.walk(func):
            if isinstance(node, ast.IfExp):
                rendered = ast.unparse(node)
                assert "else 0" not in rendered, f"{name} fabricates a success exit code: {rendered}"
                assert "is not None else 0" not in rendered, f"{name} fabricates a success exit code: {rendered}"


def test_execute_command_publishes_the_real_exit_code():
    sandbox = LocalSandbox("t1")
    ok = sandbox.execute_command("echo hello_honesty")
    assert "hello_honesty" in ok, ok
    assert "Exit Code: 0" not in ok, ok

    bad = sandbox.execute_command("exit 9")
    assert "Exit Code: 9" in bad, bad

    missing = sandbox.execute_command("definitely_not_a_real_binary_zzz")
    assert "Exit Code:" in missing, missing
    assert "Exit Code: 0" not in missing, missing


def test_execute_command_reports_stderr():
    sandbox = LocalSandbox("t1")
    out = sandbox.execute_command("echo problem_here 1>&2")
    assert "problem_here" in out, out


# ---------------------------------------------------------------------------
# The shared resolver is the only path-mapping entry point the tools use
# ---------------------------------------------------------------------------


def test_resolve_sandbox_tool_path_passes_through_for_remote_providers(tmp_path):
    remote_runtime = SimpleNamespace(state={"sandbox": {"sandbox_id": "e2b:abc"}}, context={})
    # A remote provider owns its own mapping; the path is returned unchanged.
    assert sandbox_tools.resolve_sandbox_tool_path(remote_runtime, "/anything/at/all", read_only=False) == "/anything/at/all"


def test_resolve_sandbox_tool_path_requires_thread_data_for_local(tmp_path):
    from alpha.sandbox.exceptions import SandboxRuntimeError

    local_runtime = SimpleNamespace(state={"sandbox": {"sandbox_id": "local:t1"}}, context={})
    with pytest.raises(SandboxRuntimeError):
        sandbox_tools.resolve_sandbox_tool_path(local_runtime, "/mnt/user-data/workspace/x", read_only=True)


def test_path_traversal_is_refused_by_the_shared_resolver(tmp_path):
    rt = _runtime(tmp_path / "thread")
    with pytest.raises(PermissionError):
        sandbox_tools.resolve_sandbox_tool_path(rt, "/mnt/user-data/workspace/../escape", read_only=False)


def test_outside_mapping_is_refused_by_the_shared_resolver(tmp_path):
    rt = _runtime(tmp_path / "thread")
    with pytest.raises(PermissionError):
        sandbox_tools.resolve_sandbox_tool_path(rt, str(tmp_path / "elsewhere.txt"), read_only=True)


def test_resolver_returns_the_mapped_host_path(tmp_path):
    rt = _runtime(tmp_path / "thread")
    resolved = sandbox_tools.resolve_sandbox_tool_path(rt, "/mnt/user-data/workspace/f.txt", read_only=True)
    assert Path(resolved) == (tmp_path / "thread" / "workspace" / "f.txt").resolve()


def test_resolver_refuses_an_already_resolved_host_path(tmp_path):
    """Fail-closed: a bare host path is not a mapped path, even a real one.

    This is the boundary that the old ``Path(file_path).exists()`` code walked
    straight through.
    """
    rt = _runtime(tmp_path / "thread")
    host = tmp_path / "thread" / "workspace" / "f.txt"
    host.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        sandbox_tools.resolve_sandbox_tool_path(rt, str(host), read_only=True)


def test_no_bare_exists_bypass_remains_in_the_hashline_tools():
    """The defect itself: a raw `Path(...).exists()` cannot see the mapping."""
    import ast
    import inspect

    from alpha.tools.builtins import hashline_tool

    tree = ast.parse(inspect.getsource(hashline_tool))
    # Walk executable code only: the module docstring names ``.exists()`` on
    # purpose, to record what the defect was.
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # bare string expression (module/class/function docstring)
        if isinstance(node, ast.Attribute) and node.attr == "exists":
            raise AssertionError("hashline tools must not probe the host filesystem directly")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Path":
            raise AssertionError("hashline tools must not build raw host paths")

    source = inspect.getsource(hashline_tool)
    assert "read_current_file_content" in source
    assert "resolve_sandbox_tool_path" in source


def test_process_manager_does_not_fabricate_exit_codes():
    from alpha.sandbox import process_manager

    source = Path(process_manager.__file__).read_text(encoding="utf-8")
    assert "_exit_code = -9" not in source, "kill() must not stamp a fabricated exit code"
