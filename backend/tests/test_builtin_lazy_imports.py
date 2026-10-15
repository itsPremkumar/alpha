"""The builtin package facade must stay cheap until a tool is requested."""

from __future__ import annotations

import subprocess
import sys


def test_builtin_package_import_does_not_load_tool_implementations() -> None:
    code = """
import sys
from alpha.tools.builtins import __all__
assert __all__
assert 'alpha.tools.builtins.tool_search' not in sys.modules
assert 'alpha.tools.builtins.code_agentic_core' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_builtin_export_resolves_on_demand() -> None:
    code = """
import sys
import alpha.tools.builtins as builtins
assert 'alpha.tools.builtins.tool_search_tool' not in sys.modules
tool = builtins.catalog_tool_search
assert tool is not None
assert 'alpha.tools.builtins.tool_search_tool' in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_gateway_and_prompt_imports_do_not_load_heavy_builtin_tools() -> None:
    code = """
import sys
import app.gateway.routers.checkpoints
import alpha.agents.lead_agent.prompt
assert 'alpha.tools.builtins.code_agentic_core' not in sys.modules
assert 'alpha.tools.builtins.tool_search' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_authorization_principal_import_does_not_load_sandbox_runtime() -> None:
    code = """
import sys
from alpha.authz.principal import normalize_authz_attributes
assert normalize_authz_attributes({}) == {}
assert 'alpha.authz.sandbox_authz' not in sys.modules
assert 'alpha.sandbox.exceptions' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_subagent_tool_export_survives_submodule_import_order() -> None:
    code = """
from alpha.tools.builtins.task_tool import task_tool as direct_tool
from alpha.tools.tools import _build_registries

_, subagent_tools = _build_registries()
resolved = next(tool for tool in subagent_tools if tool.name == 'task')
assert resolved is direct_tool
assert resolved.name == 'task'
assert hasattr(resolved, 'invoke')
"""
    subprocess.run([sys.executable, "-c", code], check=True)
