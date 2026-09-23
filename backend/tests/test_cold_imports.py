"""Cold-import hygiene: import order must never crash a module.

The suite dodges a real circular import with a mock executor module
(tests/conftest.py:29-41); these subprocess probes import the real modules in
a fresh interpreter so the cycle cannot come back silently. The historical
crash: executor -> authz -> tool_filter -> alpha.tools -> builtins ->
task_tool -> alpha.subagents.__getattr__ -> partially-initialized executor.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "alpha.subagents.executor",
        "alpha.tools",
        "alpha.authz",
        "alpha.agents.memory.tools",
        "alpha.agents.middlewares.learning_fork_middleware",
    ],
)
def test_cold_import(module: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"cold import {module} failed:\n{proc.stderr[-3000:]}"
