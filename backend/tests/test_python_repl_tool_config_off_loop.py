"""``python_repl`` must resolve its config off the event loop.

The tool's defence-in-depth check calls ``get_app_config()``, which resolves the
config path (``Path.cwd()`` plus the candidate files) and reads a content digest
before returning. Evaluated inline in the async tool coroutine that is blocking
IO on the event loop: the strict Blockbuster gate catches it as
``BlockingError: Blocking call to os.getcwd`` in
``tests/blocking_io/test_repl_session_offloop.py``. This is the fast, gate-free
pin of the same rule — the resolution runs on a worker thread, so a cold cache
or a slow network mount cannot stall the Gateway loop either.
"""

from __future__ import annotations

import importlib
import threading
from types import SimpleNamespace

import pytest
from langgraph.prebuilt import ToolRuntime

from alpha.sandbox.security import LOCAL_IN_PROCESS_REPL_DISABLED_MESSAGE

# ``alpha.tools.builtins`` re-exports the decorated tool object under the same
# name, shadowing the submodule; import it explicitly so ``monkeypatch`` can
# reach the module-level ``get_app_config`` reference the coroutine resolves.
tool_module = importlib.import_module("alpha.tools.builtins.python_repl_tool")

pytestmark = pytest.mark.asyncio


async def test_tool_resolves_config_on_a_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tool coroutine dispatches ``get_app_config`` to a worker thread."""
    seen: dict[str, int] = {}

    def _fake_get_app_config() -> SimpleNamespace:
        seen["thread"] = threading.get_ident()
        return SimpleNamespace(sandbox=SimpleNamespace(allow_in_process_repl=False))

    monkeypatch.setattr(tool_module, "get_app_config", _fake_get_app_config)

    runtime = ToolRuntime(
        state={},
        context={"thread_id": "offloop"},
        config={},
        stream_writer=lambda _: None,
        tool_call_id="offloop",
        store=None,
    )

    output = await tool_module.python_repl_tool.ainvoke(
        {
            "code": "raise SystemExit('must not run: the switch is off')",
            "runtime": runtime,
        }
    )

    assert output == LOCAL_IN_PROCESS_REPL_DISABLED_MESSAGE
    assert "thread" in seen, "get_app_config() was never called"
    assert seen["thread"] != threading.get_ident(), "get_app_config() ran on the event loop thread; dispatch it with asyncio.to_thread so config path resolution and the content digest never block the loop"
