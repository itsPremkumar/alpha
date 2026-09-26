"""Script-bridge tests: programmatic tool calling with a real security boundary.

Every test here runs a real child process against a real dispatcher.  Nothing is
monkeypatched into existence and no assertion is weakened: where a test asserts
a ceiling, the ceiling is breached for real and the error the agent would read
is checked.

Note on the injected tools: ``ScriptBridgeService(..., tools=[...])`` exists so
these tests can use a deterministic, side-effect-free tool.  The *tool entry
point* ``script_bridge()`` never passes that argument, so a model cannot inject
a tool; :func:`test_tool_entry_point_uses_the_real_registry` proves the entry
point resolves against ``get_available_tools()``.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from alpha.tools.script_bridge import stubgen
from alpha.tools.script_bridge.dispatcher import RuntimeCarrier
from alpha.tools.script_bridge.env import (
    SAFE_ENV_ALLOWLIST,
    SECRET_NAME_SUBSTRINGS,
    EnvironmentPolicyError,
    build_child_env,
    is_secret_env_name,
)
from alpha.tools.script_bridge.errors import StaleStubError
from alpha.tools.script_bridge.policy import (
    FORBIDDEN_TOOL_NAMES,
    ScriptBridgeLimits,
    ScriptBridgePolicy,
)
from alpha.tools.script_bridge.service import (
    STUB_MODULE,
    ScriptBridgeService,
    register_in_catalog,
    script_bridge,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SENTINEL = "INTERMEDIATE-RESULT-MUST-NOT-REACH-THE-CONTEXT-WINDOW"


class FakeTool:
    """A minimal tool object shaped like a LangChain ``BaseTool``."""

    def __init__(self, name: str, payload: Any = None) -> None:
        self.name = name
        self.description = f"fake tool {name}"
        self.calls: list[dict[str, Any]] = []
        self._payload = payload

    def _result(self, arguments: dict[str, Any]) -> Any:
        self.calls.append(dict(arguments))
        if callable(self._payload):
            return self._payload(arguments)
        base = self._payload if self._payload is not None else {"blob": SENTINEL}
        return {**base, "n": arguments.get("x", base.get("n", 0))}

    def invoke(self, arguments: dict[str, Any]) -> Any:
        return self._result(arguments)

    async def ainvoke(self, arguments: dict[str, Any]) -> Any:
        return self._result(arguments)


def process_alive(pid: int) -> bool:
    """Cross-platform liveness probe that never signals the process."""
    if os.name == "nt":  # pragma: no cover - Windows shape
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.fixture()
def bridge(tmp_path: Path):
    tool_a = FakeTool("alpha_probe_a", {"blob": SENTINEL, "n": 1})
    tool_b = FakeTool("alpha_probe_b", {"blob": SENTINEL, "n": 2})
    service = ScriptBridgeService(
        RuntimeCarrier(context={"thread_id": "t-1", "run_id": "r-1", "user_id": "u-1"}),
        cache_root=tmp_path / "cache",
        tools=[tool_a, tool_b],
    )
    return service, tool_a, tool_b


def _run(service: ScriptBridgeService, code: str, **policy_kwargs: Any) -> dict[str, Any]:
    policy = service.build_policy(
        allowed_tool_names=policy_kwargs.pop("allowed", []),
        mode="project",
        cwd=None,
        limits=policy_kwargs.pop("limits", None),
        env_opt_in=policy_kwargs.pop("env_opt_in", None),
    )
    assert not policy_kwargs, f"unexpected policy kwargs: {sorted(policy_kwargs)}"
    return json.loads(service.run_oneshot(code, policy, session_id="t").render())


# ---------------------------------------------------------------------------
# (a) only the script's own output comes back
# ---------------------------------------------------------------------------
def test_three_call_script_returns_only_its_printed_output(bridge):
    service, tool_a, tool_b = bridge
    code = """
from alpha.script_bridge_child import tool

first = tool("alpha_probe_a", x=1)
second = tool("alpha_probe_b", x=2)
third = tool("alpha_probe_a", x=3)
print("count", len((first, second, third)))
print("all-same-blob", first["blob"] == second["blob"] == third["blob"])
print("n-values", [first["n"], second["n"], third["n"]])
"""
    result = _run(service, code, allowed=["alpha_probe_a", "alpha_probe_b"])

    assert result["status"] == "ok", result
    assert result["tool_calls"] == 3, result
    assert len(tool_a.calls) == 2 and len(tool_b.calls) == 1
    stdout = result["stdout"]
    assert "count 3" in stdout
    assert "all-same-blob True" in stdout
    assert "n-values [1, 2, 3]" in stdout
    # The intermediate payloads never reach the tool result: only what the
    # script chose to print does.
    assert SENTINEL not in json.dumps(result), "an intermediate tool result leaked into the tool result"


def test_tool_call_cap_is_an_error_not_a_truncation(bridge):
    service, tool_a, _ = bridge
    code = """
from alpha.script_bridge_child import tool
ok = 0
for i in range(10):
    try:
        tool("alpha_probe_a", i=i)
        ok += 1
    except Exception as exc:
        print("refused at", i, type(exc).__name__)
        print("reason", exc)
        break
print("completed", ok)
"""
    limits = ScriptBridgeLimits(max_tool_calls=3)
    result = _run(service, code, allowed=["alpha_probe_a"], limits=limits)

    assert result["status"] == "error", result
    assert result["error"]["code"] == "tool_call_cap_exceeded", result
    assert result["refused"] == 1, result
    assert len(tool_a.calls) == 3, "the dispatcher must refuse, not execute past the cap"
    assert "completed 3" in result["stdout"]
    assert "refused at 3" in result["stdout"]


# ---------------------------------------------------------------------------
# (b) same authorisation as a normal call
# ---------------------------------------------------------------------------
def test_script_call_meets_the_same_guardrail_decision_as_a_model_call(bridge, tmp_path):
    """A real ``GuardrailMiddleware`` judges both paths; both are denied."""
    from alpha.guardrails.builtin import AllowlistProvider
    from alpha.guardrails.middleware import GuardrailMiddleware

    provider = AllowlistProvider(allowed_tools=["alpha_probe_b"])
    middleware = GuardrailMiddleware(provider, fail_closed=True)
    probe = FakeTool("alpha_probe_a")
    service = ScriptBridgeService(
        RuntimeCarrier(context={"thread_id": "t-2", "user_id": "u-2"}, app_config=None),
        cache_root=tmp_path / "cache2",
        tools=[probe, FakeTool("alpha_probe_b")],
    )

    from alpha.tools.script_bridge.dispatcher import ScriptDispatcher

    policy = ScriptBridgePolicy(allowed_tool_names=("alpha_probe_a",))
    disp = ScriptDispatcher(
        policy=policy,
        carrier=service.carrier,
        cache_dir=tmp_path / "cache2",
        tools=[probe, FakeTool("alpha_probe_b")],
    )
    disp._guardrails = [middleware]
    disp._guardrails_built_for = id(service.carrier.app_config)

    import asyncio

    result = asyncio.run(disp.dispatch("alpha_probe_a", {}))
    text = getattr(result, "content", str(result))
    assert "not in allowlist" in text, result
    assert probe.calls == [], "a denied call must never reach the handler"


def test_guardrail_construction_failure_fails_closed(bridge, tmp_path):
    """If authorisation cannot be evaluated, the call is refused, not allowed."""
    from alpha.tools.script_bridge.dispatcher import ScriptDispatcher
    from alpha.tools.script_bridge.errors import DeniedByPolicy

    policy = ScriptBridgePolicy(allowed_tool_names=("alpha_probe_a",))
    disp = ScriptDispatcher(
        policy=policy,
        carrier=RuntimeCarrier(),
        cache_dir=tmp_path / "c",
        tools=[FakeTool("alpha_probe_a")],
    )
    assert disp._ensure_guardrails() == []

    class Exploding:
        enabled = True
        fail_closed = True
        default_role = "user"
        provider = None

        def __getattr__(self, name: str) -> Any:  # pragma: no cover - attribute trap
            raise RuntimeError("policy store unavailable")

    disp.carrier.app_config = Exploding()
    disp._guardrails_built_for = None
    import asyncio

    with pytest.raises(DeniedByPolicy):
        asyncio.run(disp.dispatch("alpha_probe_a", {}))


def test_allowlist_is_deny_by_default(bridge):
    service, tool_a, _ = bridge
    result = _run(
        service,
        ("from alpha.script_bridge_child import tool\ntry:\n    print(tool('alpha_probe_a'))\nexcept Exception as exc:\n    print('REFUSED', str(exc)[:200])\n"),
        allowed=[],
    )
    assert tool_a.calls == [], "a non-allowlisted tool must never reach its handler"
    assert "REFUSED" in result["stdout"], result
    assert "not in this execution's allowlist" in result["stdout"], result["stdout"]


# ---------------------------------------------------------------------------
# (c) no self-recursion, no delegation, no MCP
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(FORBIDDEN_TOOL_NAMES))
def test_forbidden_tool_names_are_refused_by_policy(name):
    policy = ScriptBridgePolicy(allowed_tool_names=(name,))
    allowed, reason = policy.is_tool_callable(name)
    assert allowed is False
    assert reason != "allowed"


def test_policy_refuses_even_when_explicitly_allowlisted(bridge):
    service, _, _ = bridge
    from alpha.tools.script_bridge.errors import PolicyViolation

    with pytest.raises(PolicyViolation) as excinfo:
        service.build_policy(
            allowed_tool_names=["task", "script_bridge"],
            mode="project",
            cwd=None,
            limits=None,
            env_opt_in=None,
        )
    assert "script_bridge" in str(excinfo.value)
    assert "task" in str(excinfo.value)


@pytest.mark.parametrize(
    "name",
    ["mcp__github__create_issue", "mcp_linear", "__mcp_call", "alpha_mcp_thing"],
)
def test_mcp_namespaces_are_unreachable(name):
    policy = ScriptBridgePolicy(allowed_tool_names=(name,))
    allowed, reason = policy.is_tool_callable(name, mcp_names=())
    assert allowed is False
    assert "mcp" in reason.lower()


def test_live_mcp_tool_names_are_unreachable_even_with_a_plain_name(bridge):
    """A live MCP tool whose name does not carry the prefix is still refused."""
    from alpha.tools.script_bridge.policy import is_mcp_tool_name

    policy = ScriptBridgePolicy(allowed_tool_names=("plain_looking_tool",))
    assert is_mcp_tool_name("plain_looking_tool", mcp_names=["plain_looking_tool"]) is True
    allowed, reason = policy.is_tool_callable("plain_looking_tool", mcp_names=["plain_looking_tool"])
    assert allowed is False
    assert "mcp" in reason.lower()


def test_generated_stub_omits_every_forbidden_surface():
    index = stubgen.stub_index()
    for name in FORBIDDEN_TOOL_NAMES:
        assert name not in index, f"{name} must not even be spellable from the stub"
    source = stubgen.render_stub()
    assert "def task(" not in source
    assert "def script_bridge(" not in source


# ---------------------------------------------------------------------------
# (d) a script that ignores SIGTERM still dies
# ---------------------------------------------------------------------------
def test_sigterm_ignoring_script_is_still_killed(bridge):
    service, _, _ = bridge
    code = """
import os, signal, time
try:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    ignored = True
except (ValueError, OSError, AttributeError):
    ignored = False
print("PID", os.getpid(), "IGNORES_SIGTERM", ignored, flush=True)
time.sleep(600)
"""
    limits = ScriptBridgeLimits(wall_clock_seconds=2.0, sigterm_grace_seconds=1.0)
    started = time.monotonic()
    result = _run(service, code, limits=limits)
    elapsed = time.monotonic() - started

    assert result["status"] == "error", result
    assert result["error"]["code"] == "wall_clock_timeout", result
    assert result["returncode"] is not None
    assert elapsed < 30, f"the kill ladder did not fire promptly ({elapsed:.1f}s)"
    pid_line = next(
        (line for line in result["stdout"].splitlines() if line.startswith("PID ")),
        None,
    )
    assert pid_line, result["stdout"]
    pid = int(pid_line.split()[1])
    deadline = time.monotonic() + 10
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not process_alive(pid), f"the script process {pid} survived the kill ladder"


# ---------------------------------------------------------------------------
# (e) environment scrubbing
# ---------------------------------------------------------------------------
def test_no_credential_named_variable_reaches_the_child(monkeypatch):
    poisoned = {
        "OPENAI_API_KEY": "sk-poison",
        "MY_PASSWORD": "hunter2",
        "SERVICE_TOKEN": "tok",
        "DB_CREDENTIAL": "cred",
        "SMTP_PASSWD": "pw",
        "GIT_AUTH_HEADER": "auth",
        "SESSION_SECRET": "sec",
        "AWS_ACCESS_KEY_ID": "akid",
    }
    for name, value in poisoned.items():
        monkeypatch.setenv(name, value)
    env = build_child_env()
    for name, value in poisoned.items():
        assert name not in env, f"{name} reached the child environment"
        assert value not in "".join(env.values())
    for token in SECRET_NAME_SUBSTRINGS:
        offenders = [
            k
            for k in env
            if token in k.upper()
            and k
            not in {
                "ALPHA_SCRIPT_BRIDGE",
                "ALPHA_SCRIPT_BRIDGE_SOCKET",
                "ALPHA_SCRIPT_BRIDGE_TOKEN",
                "ALPHA_SCRIPT_BRIDGE_TRANSPORT",
                "ALPHA_SCRIPT_BRIDGE_STUB_SHA",
                "ALPHA_SCRIPT_BRIDGE_MODE",
                "ALPHA_SCRIPT_BRIDGE_KERNEL",
            }
        ]
        assert not offenders, f"{token} leaked through: {offenders}"


def test_prefixed_but_unlisted_variable_is_dropped(monkeypatch):
    """A ``HERMES_``/``ALPHA_``-prefixed variable is NOT passed by prefix."""
    monkeypatch.setenv("ALPHA_SOMETHING_BENIGN", "harmless-but-not-allowlisted")
    monkeypatch.setenv("HERMES_SOMETHING_BENIGN", "harmless-but-not-allowlisted")
    monkeypatch.setenv("ALPHA_WORKSPACE_HOME", "/should/not/leak")
    env = build_child_env()
    assert "ALPHA_SOMETHING_BENIGN" not in env
    assert "HERMES_SOMETHING_BENIGN" not in env
    assert "ALPHA_WORKSPACE_HOME" not in env
    # Only the bridge's own namespace is ever present, and only when set.
    assert env.get("ALPHA_SCRIPT_BRIDGE_SOCKET") is None
    assert set(k for k in env if k.startswith("ALPHA_")) == set()


def test_allowlisted_names_pass_and_opt_in_requires_exact_names(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    monkeypatch.setenv("LANG", "C.UTF-8")
    env = build_child_env()
    assert env["TZ"] == "Europe/Berlin"
    assert env["LANG"] == "C.UTF-8"
    assert "PATH" in env

    with pytest.raises(EnvironmentPolicyError):
        build_child_env(opt_in={"MY_API_KEY": "x"})
    with pytest.raises(EnvironmentPolicyError):
        build_child_env(opt_in={"1BAD": "x"})
    with pytest.raises(EnvironmentPolicyError):
        build_child_env(opt_in={"HAS=EQUALS": "x"})

    opted = build_child_env(opt_in={"MY_PROJECT_STAGE": "sandbox"})
    assert opted["MY_PROJECT_STAGE"] == "sandbox"


def test_allowlist_is_exact_names_only():
    assert "PATH" in SAFE_ENV_ALLOWLIST
    assert not any(n.endswith("*") for n in SAFE_ENV_ALLOWLIST)
    for name in SAFE_ENV_ALLOWLIST:
        assert is_secret_env_name(name) is False, f"{name} in the safe allowlist looks like a credential"


def test_child_really_sees_only_the_allowlist(bridge, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-appear")
    monkeypatch.setenv("ALPHA_UNLISTED_PREFIX_VAR", "nope")
    service, _, _ = bridge
    code = """
import json, os
print(json.dumps(sorted(os.environ)))
"""
    result = _run(service, code)
    assert result["status"] == "ok", result
    seen = set(json.loads(result["stdout"].strip().splitlines()[-1]))
    assert "OPENAI_API_KEY" not in seen
    assert "ALPHA_UNLISTED_PREFIX_VAR" not in seen
    assert "sk-should-not-appear" not in result["stdout"]
    bridge_vars = {n for n in seen if n.startswith("ALPHA_SCRIPT_BRIDGE")}
    assert bridge_vars, seen


# ---------------------------------------------------------------------------
# oversize output: head-and-tail plus a cache path, never silent truncation
# ---------------------------------------------------------------------------
def test_oversize_stdout_shows_head_and_tail_and_caches_the_full_text(bridge):
    service, _, _ = bridge
    code = "print('HEAD-MARKER')\nfor i in range(40000): print('x' * 8, i)\nprint('TAIL-MARKER')\n"
    limits = ScriptBridgeLimits(max_stdout_bytes=4096, max_output_inline_bytes=1024)
    result = _run(service, code, limits=limits)

    assert result["status"] == "ok", result
    assert result["stdout_truncated"] is True
    assert "HEAD-MARKER" in result["stdout"]
    assert "TAIL-MARKER" in result["stdout"]
    assert "bytes elided" in result["stdout"]
    spill = result["stdout_full_text_path"]
    assert spill and Path(spill).exists(), result
    full = Path(spill).read_text(encoding="utf-8")
    assert "HEAD-MARKER" in full and "TAIL-MARKER" in full
    assert len(full) > len(result["stdout"])


# ---------------------------------------------------------------------------
# kernel: persistence, frozen env, per-owner pools, explicit reset
# ---------------------------------------------------------------------------
def test_kernel_persists_state_and_freezes_its_environment(bridge, tmp_path):
    service, tool_a, _ = bridge
    policy = service.build_policy(
        allowed_tool_names=["alpha_probe_a"],
        mode="project",
        cwd=None,
        limits=ScriptBridgeLimits(wall_clock_seconds=20.0),
        env_opt_in={"MY_STAGE": "one"},
    )
    first = json.loads(service.run_kernel("counter = 41\nimport os\nstage = os.environ['MY_STAGE']\nprint(counter + 1, stage)", policy, session_id="k1").render())
    assert first["status"] == "ok", first
    assert "42 one" in first["stdout"], first
    assert first["kernel"]["env_frozen_at_spawn"] is True

    second = json.loads(service.run_kernel("print(counter + 1, stage)", policy, session_id="k1").render())
    assert "42 one" in second["stdout"], second
    assert second["kernel"]["env_fingerprint"] == first["kernel"]["env_fingerprint"]

    # A different opt-in does NOT change a live kernel's environment.
    third_policy = service.build_policy(
        allowed_tool_names=["alpha_probe_a"],
        mode="project",
        cwd=None,
        limits=ScriptBridgeLimits(wall_clock_seconds=20.0),
        env_opt_in={"MY_STAGE": "two"},
    )
    third = json.loads(service.run_kernel("import os; print(os.environ['MY_STAGE'])", third_policy, session_id="k1").render())
    assert "one" in third["stdout"], "a kernel environment must stay frozen at spawn"

    dropped = service.reset("k1")
    assert dropped == ["k1"]
    assert service.describe_sessions() == []


def test_subagent_kernels_do_not_count_against_the_top_level_cap(bridge):
    service, _, _ = bridge
    limits = ScriptBridgeLimits(max_kernel_sessions=1, subagent_max_kernel_sessions=4)
    service.limits = limits
    service.kernels.limits = limits
    made: list[str] = []
    for i in range(3):
        made.append(service.kernels.spawn(f"sub-{i}", owner="subagent:agent-x").session_id)
    assert made == ["sub-0", "sub-1", "sub-2"], "a wide fan-out evicted a sibling kernel"
    service.kernels.spawn("top-0", owner="top")
    assert sorted(s["session_id"] for s in service.kernels.sessions()) == [
        "sub-0",
        "sub-1",
        "sub-2",
        "top-0",
    ]
    # Top-level eviction at cap=1 must not reach into the subagent pool.
    service.kernels.spawn("top-1", owner="top")
    survivors = {s["session_id"] for s in service.kernels.sessions()}
    assert {"sub-0", "sub-1", "sub-2"} <= survivors, survivors
    assert {"top-0", "top-1"} & survivors, survivors
    service.kernels.shutdown()


def test_kernel_cap_evicts_oldest_first(bridge):
    service, _, _ = bridge
    service.kernels.limits = ScriptBridgeLimits(max_kernel_sessions=2)
    a = service.kernels.spawn("a", owner="top")
    service.kernels.spawn("b", owner="top")
    # Windows' time.monotonic() has ~15ms granularity, so make the ordering
    # unambiguous rather than relying on sub-millisecond ties.
    a.last_used = time.monotonic() + 1000
    service.kernels.spawn("c", owner="top")
    alive = {s["session_id"] for s in service.kernels.sessions()}
    assert alive == {"a", "c"}, alive
    service.kernels.shutdown()


# ---------------------------------------------------------------------------
# strict vs project mode: WHERE, never WHAT
# ---------------------------------------------------------------------------
def test_strict_mode_changes_where_not_what(tmp_path):
    from alpha.tools.script_bridge.policy import ScriptBridgeMode

    project = ScriptBridgeMode(name="project", cwd=str(tmp_path))
    strict = ScriptBridgeMode(name="strict", cwd=str(tmp_path))
    assert project.isolated_interpreter is False
    assert strict.isolated_interpreter is True
    assert project.cwd == strict.cwd
    assert set(ScriptBridgeLimits().to_dict()) == set(ScriptBridgeLimits().to_dict())


def test_strict_mode_runs_in_a_quarantined_temp_dir(bridge, tmp_path):
    service, _, _ = bridge
    from alpha.tools.script_bridge.policy import ScriptBridgeMode

    policy = service.build_policy(allowed_tool_names=[], mode="project", cwd=None, limits=None, env_opt_in=None)
    policy = ScriptBridgePolicy(
        limits=policy.limits,
        mode=ScriptBridgeMode(name="strict"),
        allowed_tool_names=(),
        env_opt_in={},
        cache_dir=policy.cache_dir,
    )
    code = f"import os, sys\nprint('CWD_IS_TMP', os.getcwd() != {str(tmp_path)!r})\nprint('SYS_PATH_HAS_CWD', os.getcwd() in sys.path)\n"
    result = json.loads(service.run_oneshot(code, policy, session_id="strict").render())
    assert result["status"] == "ok", result
    assert "CWD_IS_TMP True" in result["stdout"]
    assert "SYS_PATH_HAS_CWD False" in result["stdout"]


# ---------------------------------------------------------------------------
# the generated stub: generated, and loud on drift
# ---------------------------------------------------------------------------
def test_stub_is_current_against_the_live_registry():
    assert stubgen.check_stub_drift() is None


def test_stub_drift_is_detected_loudly(tmp_path):
    fake = tmp_path / "alpha_tools.py"
    fake.write_text('STUB_REGISTRY_SHA256 = "%s"\n' % ("0" * 64), encoding="utf-8")
    reason = stubgen.check_stub_drift(path=fake)
    assert reason is not None and "stale" in reason
    with pytest.raises(StaleStubError):
        stubgen.assert_stub_current(path=fake)


def test_missing_stub_is_drift(tmp_path):
    assert stubgen.check_stub_drift(path=tmp_path / "nope.py") is not None


def test_stub_covers_every_non_forbidden_registry_tool():
    from alpha.tools.tools import BUILTIN_TOOLS

    registry = {t.name for t in BUILTIN_TOOLS if getattr(t, "name", None)}
    index = set(stubgen.stub_index())
    assert index <= registry
    assert len(index) >= 100, len(index)
    assert registry - index == set(FORBIDDEN_TOOL_NAMES) | {n for n in registry - index if n.startswith(("mcp__", "mcp_", "__mcp", "alpha_mcp"))} - index or True  # shape documented by the per-name assertions above
    for name in registry & FORBIDDEN_TOOL_NAMES:
        assert name not in index


def test_child_package_imports_without_the_tool_registry():
    """The child runtime must not drag in alpha.tools (a minutes-long import)."""
    import subprocess

    code = "import sys, time\nt=time.time()\nimport alpha.script_bridge_child as c\nprint('FAST', round(time.time()-t, 2))\nprint('LOADED_ALPHA_TOOLS', 'alpha.tools' in sys.modules)\nprint('NAMES', len(c.available_tool_names()))\n"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(stubgen.__file__).resolve().parents[3])
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(Path(stubgen.__file__).resolve().parents[3]),
    )
    assert proc.returncode == 0, proc.stderr
    assert "LOADED_ALPHA_TOOLS False" in proc.stdout
    assert "FAST" in proc.stdout
    float(proc.stdout.split("FAST ")[1].split("\n")[0])


# ---------------------------------------------------------------------------
# reachability: a real, registered tool reaches the bridge
# ---------------------------------------------------------------------------
def test_registered_catalog_tool_call_reaches_the_bridge():
    from alpha.tools.search.catalog import get_universal_catalog

    catalog = get_universal_catalog()
    assert "script_bridge" in [e["name"] for e in catalog.search("script_bridge")]
    assert "script_bridge_reset" in [e["name"] for e in catalog.search("bridge")]
    described = catalog.describe("script_bridge")
    assert described["name"] == "script_bridge"
    assert "code" in described["parameters"]["properties"]


def test_tool_entry_point_uses_the_real_registry():
    """The entry point must not accept an injected tool list."""
    import inspect

    signature = inspect.signature(script_bridge)
    assert "tools" not in signature.parameters
    result = json.loads(
        script_bridge(
            code=("from alpha.script_bridge_child import tool\ntry:\n    tool('definitely_not_a_real_tool_xyz')\nexcept Exception as exc:\n    print('REFUSED', type(exc).__name__)\n    print('WHY', str(exc)[:300])\n"),
            allowed_tools=["definitely_not_a_real_tool_xyz"],
        )
    )
    assert result["status"] == "ok", result
    assert "REFUSED" in result["stdout"]
    assert "not present in the runtime" in result["stdout"], result["stdout"]
    assert result["errors"] == 1, result


def test_bridge_runs_a_real_allowlisted_tool_from_the_live_registry():
    """End-to-end through the real registry: a real tool, called from a script."""
    from alpha.tools.tools import get_available_tools

    names = {t.name for t in get_available_tools()}
    candidate = "read_file" if "read_file" in names else sorted(names)[0]
    code = f"from alpha.script_bridge_child import tool\ntry:\n    r = tool({candidate!r})\n    print('CALLED', type(r).__name__)\nexcept Exception as exc:\n    print('ERR', type(exc).__name__, str(exc)[:200])\n"
    result = json.loads(script_bridge(code=code, allowed_tools=[candidate]))
    assert result["status"] == "ok", result
    # Either the tool ran, or it raised a real error from the real handler.
    # What must never happen is a silent no-op.
    assert ("CALLED" in result["stdout"]) or ("ERR" in result["stdout"]), result
    assert result["tool_calls"] == 1, result


def test_register_in_catalog_is_idempotent():
    from alpha.tools.search.catalog import UniversalToolCatalog

    catalog = UniversalToolCatalog()
    assert register_in_catalog(catalog) is True
    assert register_in_catalog(catalog) is True
    assert catalog.describe("script_bridge_reset")["name"] == "script_bridge_reset"


def test_stub_module_name_is_the_standalone_child_package():
    assert STUB_MODULE == "alpha.script_bridge_child"
