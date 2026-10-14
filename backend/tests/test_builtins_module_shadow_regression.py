"""A direct submodule import must not leave the module in the tool registry.

``alpha.tools.builtins`` is a PEP-562 lazy facade: ``__getattr__`` resolves a
tool name on first access and caches the *tool* into the package globals. But a
direct submodule import (``from alpha.tools.builtins.cognitive_memory_tool import
cognitive_memory_tool``) binds the package attribute to the *module*, so the
facade's ``__getattr__`` never fires for that name and ``_build_registries()``
would pick up the module instead of the tool. The registry then crashes downstream
(``t.name`` on a module raises ``AttributeError``).

``_build_registries`` now unwraps any module-shaped entry back to its own tool
export, so the registry is correct regardless of which submodule a caller (or a
test) imported first. This keeps the AST-parsed literal, its order, and the
``_deduplicate_tools`` pass exactly as they were, so ``contracts/feature_manifest.json``
and the capability counts are unaffected.
"""

from __future__ import annotations

import subprocess
import sys


def test_direct_submodule_import_does_not_leave_module_in_registry() -> None:
    code = """
from types import ModuleType

from alpha.tools.builtins.cognitive_memory_tool import cognitive_memory_tool
from alpha.tools.tools import _build_registries

builtins, _ = _build_registries()
cog = [tool for tool in builtins if getattr(tool, "name", None) == "cognitive_memory_tool"]
assert len(cog) == 1, f"expected one cognitive_memory_tool, got {cog!r}"
assert not isinstance(cog[0], ModuleType), "registry entry is the module, not the tool"
assert cog[0] is cognitive_memory_tool, "registry entry is not the tool the caller imported"
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_registry_has_no_module_shaped_entries_after_any_submodule_import() -> None:
    code = """
from types import ModuleType

# Import several self-named submodules directly, the way tests and some callers do.
from alpha.tools.builtins.cognitive_memory_tool import cognitive_memory_tool
from alpha.tools.builtins.task_tool import task_tool
from alpha.tools.tools import _build_registries

builtins, subagent = _build_registries()
modules = [entry for entry in builtins + subagent if isinstance(entry, ModuleType)]
assert not modules, f"registry contains modules, not tools: {modules!r}"
assert any(getattr(tool, "name", None) == "cognitive_memory_tool" for tool in builtins)
assert any(getattr(tool, "name", None) == "task" for tool in subagent)
"""
    subprocess.run([sys.executable, "-c", code], check=True)
