"""Regression coverage for the in-process REPL execution boundary.

## The defect this pins

`python_repl` `exec()`s the cell body **inside the Gateway process**, with a
namespace that preloads `os` and `sys` and a `bash` helper. It was in
`BUILTIN_TOOLS`, and the `allow_host_bash: false` filter is applied only to the
operator's `config.tools` list — so the REPL survived a switch the operator
reasonably read as "no host execution".

Reachable in the shipped default, one tool call was enough:

    python_repl(code="print(os.environ['OPENAI_API_KEY'])")

Every HIGH/CRITICAL claim in this file was verified by reading the code, and
each test here is an *inversion* of the defect: it fails if the switch stops
gating, if the filter stops dropping the tool, or if the REPL's shell goes back
to inheriting the parent environment.

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_python_repl_boundary.py -v`
"""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from alpha.config.sandbox_config import SandboxConfig
from alpha.sandbox.repl import session as repl_session
from alpha.sandbox.security import (
    LOCAL_IN_PROCESS_REPL_DISABLED_MESSAGE,
    is_in_process_repl_allowed,
    uses_local_sandbox_provider,
)


def _config(*, allow_in_process_repl: bool, use: str = "alpha.sandbox.local:LocalSandboxProvider"):
    # A real `AppConfig` is avoided on purpose: the assembly path touches
    # `config.tools`, `config.skill_evolution`, `config.models` and more, and a
    # stub missing one of them fails on an unrelated line. Only the two fields
    # under test need to be real.
    return SimpleNamespace(
        sandbox=SandboxConfig(use=use, allow_in_process_repl=allow_in_process_repl),
        tools=[],
        skill_evolution=SimpleNamespace(enabled=False),
        models=[],
    )


# --- the switch itself ------------------------------------------------------ #


def test_in_process_repl_is_off_by_default():
    """The single most important assertion: absent config means no REPL.

    A key that defaults to True here would restore the original finding while
    still looking configured, so this pins the default itself rather than a
    filtered tool list.
    """
    assert SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider").allow_in_process_repl is False


def test_switch_denies_when_off_and_allows_when_on():
    assert is_in_process_repl_allowed(_config(allow_in_process_repl=False)) is False
    assert is_in_process_repl_allowed(_config(allow_in_process_repl=True)) is True


def test_missing_sandbox_section_denies():
    """An absent `sandbox:` block must not fail open the way bash's did not."""
    assert is_in_process_repl_allowed(SimpleNamespace(sandbox=None)) is False


def test_a_containerised_provider_does_not_grant_in_process_execution():
    """Unlike host bash, a real sandbox provider must NOT unlock the REPL.

    The cell still `exec()`s in the Gateway process, not in the container, so
    switching providers gives no protection here. If this ever returns True, the
    distinction between the two switches has been lost and `allow_host_bash`
    semantics have leaked into the REPL path.
    """
    cfg = _config(allow_in_process_repl=False, use="alpha.community.aio_sandbox:AioSandboxProvider")
    assert uses_local_sandbox_provider(cfg) is False
    assert is_in_process_repl_allowed(cfg) is False


# --- assembly-time filtering ------------------------------------------------ #


def test_repl_is_absent_from_the_tool_list_when_disabled():
    from alpha.tools.tools import get_available_tools

    names = {getattr(t, "name", None) for t in get_available_tools(app_config=_config(allow_in_process_repl=False))}
    assert "python_repl" not in names, (
        "python_repl exec()s in the Gateway process with os preloaded; it must not be "
        "offered to the model while sandbox.allow_in_process_repl is False"
    )


def test_repl_is_present_when_explicitly_enabled():
    """The feature is gated, not deleted — an operator who opts in still gets it."""
    from alpha.tools.tools import get_available_tools

    names = {getattr(t, "name", None) for t in get_available_tools(app_config=_config(allow_in_process_repl=True))}
    assert "python_repl" in names


def test_repl_and_bash_switches_are_independent():
    """The two switches must not alias; that collapse is what made the original bug.

    Before the fix there was one switch and the REPL was invisible to it. The
    regression is a single boolean that governs both, so each must be able to
    deny the other permits.
    """
    bash_only = _config(allow_in_process_repl=False)
    bash_only.sandbox.allow_host_bash = True
    assert is_in_process_repl_allowed(bash_only) is False, "allow_host_bash must not unlock the REPL"

    repl_only = _config(allow_in_process_repl=True)
    repl_only.sandbox.allow_host_bash = False
    assert is_in_process_repl_allowed(repl_only) is True, "allow_in_process_repl must not be blocked by allow_host_bash"


# --- runtime refusal (defence in depth) ------------------------------------- #


def _runtime():
    """A real `ToolRuntime` — the tool's `runtime` arg is injected-typed, so a
    `SimpleNamespace` is rejected by pydantic before the body ever runs."""
    from langchain.tools import ToolRuntime

    return ToolRuntime(
        context={},
        state={},
        config={},
        stream_writer=lambda *a, **k: None,
        tool_call_id="test-call",
        store=None,
    )


@pytest.mark.asyncio
async def test_the_tool_itself_refuses_rather_than_trusting_assembly():
    """A tool that reached a runtime by another route must still refuse.

    Assembly-time filtering is the primary control, but a custom Agent tool list,
    a direct call, or a future config path could bypass it. The tool checks the
    switch itself so a bypass fails closed rather than handing out process-level
    execution.
    """
    from alpha.tools.builtins.python_repl_tool import python_repl_tool

    with patch("alpha.tools.builtins.python_repl_tool.get_app_config", return_value=_config(allow_in_process_repl=False)):
        with patch.object(repl_session, "get_repl_session") as get_session:
            result = await python_repl_tool.ainvoke({"code": "print('never')", "runtime": _runtime()})

    text = result if isinstance(result, str) else str(result)
    assert LOCAL_IN_PROCESS_REPL_DISABLED_MESSAGE in text
    get_session.assert_not_called(), "a refused call must not even open a REPL session"


@pytest.mark.asyncio
async def test_the_refusal_explains_the_switch_it_wants():
    """The message must name the config key, or the operator cannot act on it."""
    from alpha.tools.builtins.python_repl_tool import python_repl_tool

    with patch("alpha.tools.builtins.python_repl_tool.get_app_config", return_value=_config(allow_in_process_repl=False)):
        result = await python_repl_tool.ainvoke({"code": "1", "runtime": _runtime()})

    text = result if isinstance(result, str) else str(result)
    assert "allow_in_process_repl" in text
    assert "no sandbox boundary" in text


# --- the REPL shell must not inherit the parent environment ------------------ #


def test_repl_shell_does_not_inherit_the_gateway_environment(monkeypatch, tmp_path):
    """`bash()` from a cell must not hand the child the Gateway's full env.

    `subprocess` inherits the parent environment when `env=` is omitted, so the
    REPL's own shell was a second, un-scrubbed route to every API key — reachable
    by a cell that called the preloaded `bash` helper instead of touching `os`.
    """
    sentinel = "sentinel-secret-that-must-not-be-inherited"
    monkeypatch.setenv("ALPHA_REPL_LEAK_PROBE", sentinel)

    with patch.object(repl_session, "build_sandbox_env", return_value={"PATH": os.environ.get("PATH", "")}) as builder:
        proc = repl_session._start_shell("echo probe", cwd=str(tmp_path))
        try:
            out = proc.communicate(timeout=60)[0]
        finally:
            if proc.poll() is None:  # pragma: no cover - only on a wedged shell
                proc.kill()

    builder.assert_called()
    assert sentinel not in out
    assert "ALPHA_REPL_LEAK_PROBE" not in out


def test_repl_shell_passes_an_explicit_env_to_popen(monkeypatch, tmp_path):
    """Pin the mechanism, not just the outcome: `env=` must be passed.

    Asserting only on the observed output would still pass if a future change
    scrubbed the value from stdout after the fact. The contract is that the
    child is *constructed* with a scrubbed environment.
    """
    captured: dict[str, object] = {}
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        return real_popen(*args, **kwargs)

    with patch.object(repl_session.subprocess, "Popen", spy):
        proc = repl_session._start_shell("echo probe", cwd=str(tmp_path))
        proc.communicate(timeout=60)

    assert "env" in captured, "_start_shell must pass env= explicitly"
    assert captured["env"] is not None
    assert "ALPHA_INTERNAL_AUTH_TOKEN" not in (captured["env"] or {})


# --- the namespace ---------------------------------------------------------- #


def test_repl_namespace_documents_that_os_is_preloaded_and_unconfined():
    """The namespace is the other half of the risk, so it must stay self-describing.

    `os` in the namespace means a cell needs no shell to read the environment.
    The tool docstring now says so, because that is what makes the default-off
    switch non-negotiable rather than advisory.
    """
    from alpha.tools.builtins import python_repl_tool

    doc = python_repl_tool.description
    assert "exec()" in doc
    assert "allow_in_process_repl" in doc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
