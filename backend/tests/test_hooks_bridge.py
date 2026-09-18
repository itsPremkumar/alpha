"""Tests for the Claude/Codex-style shell-hook bridge."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from langchain_core.messages import HumanMessage, ToolMessage

from agent_workspace.agents.middlewares.hooks_bridge_middleware import (
    HookDecision,
    HooksBridgeMiddleware,
    _matcher_matches,
)
from agent_workspace.config.app_config import AppConfig
from agent_workspace.config.hooks_config import HooksConfig
from agent_workspace.config.model_config import ModelConfig
from agent_workspace.config.sandbox_config import SandboxConfig

PY = f'"{sys.executable}"'


def _write_hooks(path, document: Any) -> str:
    hooks_path = str(path / "hooks.json")
    with open(hooks_path, "w", encoding="utf-8") as handle:
        if isinstance(document, str):
            handle.write(document)
        else:
            json.dump(document, handle)
    return hooks_path


def _command_hooks(matcher: Any, *commands: str, timeout: Any = None) -> dict:
    entries = [{"type": "command", "command": command} for command in commands]
    if timeout is not None:
        for entry in entries:
            entry["timeout"] = timeout
    return {"hooks": {"PreToolUse": [{"matcher": matcher, "hooks": entries}]}}


def _middleware(hooks_path: str | None, **kwargs) -> HooksBridgeMiddleware:
    return HooksBridgeMiddleware(config=HooksConfig(enabled=True, hooks_path=hooks_path, **kwargs))


def _request(tool_name: str = "bash", args: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": tool_name, "args": args or {"command": "ls"}, "id": "call-1"})


def _ok_handler(calls: list | None = None):
    def _handler(request):
        if calls is not None:
            calls.append(request)
        return ToolMessage(content="result-ok", tool_call_id="call-1", name="bash")

    return _handler


def _async_ok_handler(calls: list | None = None):
    async def _handler(request):
        if calls is not None:
            calls.append(request)
        return ToolMessage(content="result-ok", tool_call_id="call-1", name="bash")

    return _handler


class TestMatcher:
    def test_empty_matcher_matches_all(self):
        assert _matcher_matches(None, "bash") is True
        assert _matcher_matches("", "bash") is True
        assert _matcher_matches("   ", "bash") is True

    def test_exact_and_glob_and_alternation(self):
        assert _matcher_matches("bash", "bash") is True
        assert _matcher_matches("bash", "read_file") is False
        assert _matcher_matches("read_*", "read_file") is True
        assert _matcher_matches("bash|read_file", "read_file") is True
        assert _matcher_matches("bash|read_file", "write_file") is False
        assert _matcher_matches(123, "bash") is False


class TestPreToolUse:
    def test_disabled_passes_through_without_spawning(self, tmp_path):
        hooks_path = _write_hooks(tmp_path, _command_hooks("bash", f'{PY} -c "import sys; sys.exit(2)"'))
        middleware = HooksBridgeMiddleware(config=HooksConfig(enabled=False, hooks_path=hooks_path))
        calls: list = []
        result = middleware.wrap_tool_call(_request(), _ok_handler(calls))
        assert isinstance(result, ToolMessage) and result.content == "result-ok"
        assert len(calls) == 1

    def test_missing_file_passes_through(self):
        middleware = _middleware("/nonexistent/hooks.json")
        calls: list = []
        result = middleware.wrap_tool_call(_request(), _ok_handler(calls))
        assert result.content == "result-ok"
        assert len(calls) == 1

    def test_exit_zero_executes(self, tmp_path):
        hooks_path = _write_hooks(tmp_path, _command_hooks("bash", f'{PY} -c "import sys; sys.exit(0)"'))
        calls: list = []
        result = _middleware(hooks_path).wrap_tool_call(_request(), _ok_handler(calls))
        assert result.content == "result-ok"
        assert len(calls) == 1

    def test_exit_two_blocks_with_stderr_reason(self, tmp_path):
        hooks_path = _write_hooks(tmp_path, _command_hooks("bash", f"{PY} -c \"import sys; sys.stderr.write('no shell today'); sys.exit(2)\""))
        calls: list = []
        result = _middleware(hooks_path).wrap_tool_call(_request(), _ok_handler(calls))
        assert isinstance(result, ToolMessage)
        assert result.status == "error"
        assert "no shell today" in result.content
        assert result.name == "bash"
        assert calls == []
        trail = result.additional_kwargs.get("agent_workspace_tool_transforms", [])
        assert trail and trail[-1]["kind"] == "hooks_bridge_blocked"

    def test_stdout_json_block_decision(self, tmp_path):
        script = "import sys,json; sys.stdout.write(json.dumps({'decision':'block','reason':'json-no'}))"
        hooks_path = _write_hooks(tmp_path, _command_hooks("bash", f'{PY} -c "{script}"'))
        result = _middleware(hooks_path).wrap_tool_call(_request(), _ok_handler([]))
        assert result.status == "error"
        assert "json-no" in result.content

    def test_matcher_mismatch_executes(self, tmp_path):
        hooks_path = _write_hooks(tmp_path, _command_hooks("write_file", f'{PY} -c "import sys; sys.exit(2)"'))
        calls: list = []
        result = _middleware(hooks_path).wrap_tool_call(_request("bash"), _ok_handler(calls))
        assert result.content == "result-ok"
        assert len(calls) == 1

    def test_hook_receives_tool_payload_on_stdin(self, tmp_path):
        out_file = (tmp_path / "seen.txt").as_posix()
        script = f"import sys,json; d=json.load(sys.stdin); open(r'{out_file}','w').write(d['tool_name'] + ':' + d['tool_input']['command'])"
        hooks_path = _write_hooks(tmp_path, _command_hooks("bash", f'{PY} -c "{script}"'))
        _middleware(hooks_path).wrap_tool_call(_request("bash", {"command": "ls -la"}), _ok_handler([]))
        with open(tmp_path / "seen.txt", encoding="utf-8") as handle:
            assert handle.read() == "bash:ls -la"

    def test_timeout_fails_open_by_default(self, tmp_path):
        hooks_path = _write_hooks(tmp_path, _command_hooks("bash", f'{PY} -c "import time; time.sleep(5)"', timeout=0.3))
        calls: list = []
        result = _middleware(hooks_path, default_timeout_seconds=0.3).wrap_tool_call(_request(), _ok_handler(calls))
        assert result.content == "result-ok"
        assert len(calls) == 1

    def test_timeout_blocks_when_fail_closed(self, tmp_path):
        hooks_path = _write_hooks(tmp_path, _command_hooks("bash", f'{PY} -c "import time; time.sleep(5)"', timeout=0.3))
        result = _middleware(hooks_path, default_timeout_seconds=0.3, fail_open=False).wrap_tool_call(_request(), _ok_handler([]))
        assert result.status == "error"

    def test_malformed_file_passes_through(self, tmp_path):
        hooks_path = _write_hooks(tmp_path, "{not json")
        calls: list = []
        result = _middleware(hooks_path).wrap_tool_call(_request(), _ok_handler(calls))
        assert result.content == "result-ok"
        assert len(calls) == 1

    def test_non_command_entries_skipped(self, tmp_path):
        document = {"hooks": {"PreToolUse": [{"matcher": "bash", "hooks": [{"type": "prompt", "prompt": "think harder"}]}]}}
        hooks_path = _write_hooks(tmp_path, document)
        calls: list = []
        result = _middleware(hooks_path).wrap_tool_call(_request(), _ok_handler(calls))
        assert result.content == "result-ok"
        assert len(calls) == 1

    def test_async_block_and_passthrough(self, tmp_path):
        block_path = _write_hooks(tmp_path, _command_hooks("bash", f'{PY} -c "import sys; sys.exit(2)"'))
        middleware = _middleware(block_path)

        async def _run():
            calls: list = []
            blocked = await middleware.awrap_tool_call(_request(), _async_ok_handler(calls))
            assert blocked.status == "error"
            assert calls == []
            ok_path = _write_hooks(tmp_path, _command_hooks("write_file", f'{PY} -c "import sys; sys.exit(2)"'))
            ok_result = await _middleware(ok_path).awrap_tool_call(_request("bash"), _async_ok_handler(calls))
            assert ok_result.content == "result-ok"
            assert len(calls) == 1

        asyncio.run(_run())


class TestPostToolUse:
    def test_post_hook_observes_result(self, tmp_path):
        out_file = (tmp_path / "resp.txt").as_posix()
        script = f"import sys,json; d=json.load(sys.stdin); open(r'{out_file}','w').write(d['hook_event_name'] + ':' + d['tool_response'])"
        document = {"hooks": {"PostToolUse": [{"matcher": "bash", "hooks": [{"type": "command", "command": f'{PY} -c "{script}"'}]}]}}
        hooks_path = _write_hooks(tmp_path, document)
        _middleware(hooks_path).wrap_tool_call(_request(), _ok_handler([]))
        with open(tmp_path / "resp.txt", encoding="utf-8") as handle:
            assert handle.read() == "PostToolUse:result-ok"


class TestLifecycleHooks:
    def _event_recorder(self, tmp_path, event: str) -> tuple[str, str]:
        event_dir = tmp_path / event
        event_dir.mkdir(exist_ok=True)
        out_file = (event_dir / "out.txt").as_posix()
        script = f"import sys,json; d=json.load(sys.stdin); open(r'{out_file}','w').write(d['hook_event_name'])"
        document = {"hooks": {event: [{"hooks": [{"type": "command", "command": f'{PY} -c "{script}"'}]}]}}
        return _write_hooks(event_dir, document), out_file

    def test_session_start_and_end_fire(self, tmp_path):
        start_path, start_file = self._event_recorder(tmp_path, "SessionStart")
        end_path, end_file = self._event_recorder(tmp_path, "SessionEnd")
        middleware = _middleware(start_path)
        middleware.before_agent({}, MagicMock())
        with open(start_file, encoding="utf-8") as handle:
            assert handle.read() == "SessionStart"
        _middleware(end_path).after_agent({}, MagicMock())
        with open(end_file, encoding="utf-8") as handle:
            assert handle.read() == "SessionEnd"

    def test_user_prompt_submit_fires_with_prompt(self, tmp_path):
        out_file = (tmp_path / "prompt.txt").as_posix()
        script = f"import sys,json; d=json.load(sys.stdin); open(r'{out_file}','w').write(d['prompt'])"
        document = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": f'{PY} -c "{script}"'}]}]}}
        hooks_path = _write_hooks(tmp_path, document)
        state = {"messages": [HumanMessage(content="hello world")]}
        assert _middleware(hooks_path).before_model(state, MagicMock()) is None
        with open(tmp_path / "prompt.txt", encoding="utf-8") as handle:
            assert handle.read() == "hello world"


class TestBridgeConfig:
    def test_release_policy_exposes_path_not_contents(self, tmp_path):
        hooks_path = _write_hooks(tmp_path, _command_hooks("bash", "echo hi"))
        params = _middleware(hooks_path).release_policy_parameters()
        assert params["hooks_path"] == hooks_path
        assert params["enabled"] is True
        assert "echo hi" not in json.dumps(params)

    def test_from_app_config(self):
        middleware = HooksBridgeMiddleware.from_app_config(object())
        assert middleware._config.enabled is False

        class _App:
            from agent_workspace.config.hooks_config import HooksConfig as _HC

            hooks = _HC(enabled=True, hooks_path="/tmp/h.json")

        assert HooksBridgeMiddleware.from_app_config(_App())._config.enabled is True

    def test_decision_default_proceeds(self):
        assert HookDecision().proceed is True


class TestBridgeAssembly:
    def _app_config(self, **hooks_kwargs) -> AppConfig:
        return AppConfig(
            models=[
                ModelConfig(
                    name="m",
                    display_name="m",
                    description=None,
                    use="langchain_openai:ChatOpenAI",
                    model="m",
                    supports_thinking=False,
                    supports_vision=False,
                )
            ],
            sandbox=SandboxConfig(use="agent_workspace.sandbox.local:LocalSandboxProvider"),
            hooks=HooksConfig(**hooks_kwargs),
        )

    def test_enabled_hooks_appends_bridge_innermost(self):
        from agent_workspace.agents.middlewares.tool_error_handling_middleware import _build_runtime_middlewares

        chain = _build_runtime_middlewares(
            app_config=self._app_config(enabled=True, hooks_path="/nonexistent/hooks.json"),
            include_uploads=False,
            include_dangling_tool_call_patch=False,
        )
        assert isinstance(chain[-1], HooksBridgeMiddleware)

    def test_disabled_by_default_bridge_absent(self):
        from agent_workspace.agents.middlewares.tool_error_handling_middleware import _build_runtime_middlewares

        chain = _build_runtime_middlewares(
            app_config=self._app_config(),
            include_uploads=False,
            include_dangling_tool_call_patch=False,
        )
        assert not any(isinstance(middleware, HooksBridgeMiddleware) for middleware in chain)
