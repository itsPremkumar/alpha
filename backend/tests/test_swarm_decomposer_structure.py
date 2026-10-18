"""Structural guard for ``alpha/swarm/decomposer.py``.

Two promises this scan holds, both written down in Phase 1 item 1.3 of
``docs/BOT_MODE_MULTI_AGENT_MASTER_PLAN.md``:

1. **Module-level imports live at the top.**  The imports of
   ``planner``/``strategy``/``topology`` used to sit mid-file behind
   ``# noqa: E402``; that is the shape this test rejects, so a later edit
   cannot quietly reintroduce a mid-file import.
2. **Exactly one definition of each symbol.**  Every top-level name the module
   binds is bound once.  The single recorded exception is the quarantine defect
   (plan section 9, item 2): the shadowed duplicate capability-tier block at
   ``:100-157`` is *retained in place* on purpose, so the scan allows exactly
   that pair of names and fails on any *other* duplicated definition.  That is
   what turns the guard into a real guard: removing the recorded duplicate is
   fine, adding a new shadowed definition is not.

The duplicate block is documented, not endorsed.  The scan names the names and
points at the plan so a future editor who sees it knows it is deliberate.
"""

from __future__ import annotations

import ast
from pathlib import Path

from alpha.swarm.decomposer import SwarmTaskDecomposer

DECOMPOSER = Path(__file__).resolve().parents[1] / "packages/harness/alpha/swarm/decomposer.py"

#: The only top-level names the plan permits to be bound more than once.  The
#: second binding shadows the first; the two are functionally identical, and
#: the plan (section 9, item 2) records the duplicate as a known defect that is
#: fixed only with explicit approval.  Any *other* shadowed name is a new
#: regression.
KNOWN_SHADOWED = frozenset({"_CAPABILITY_TIER", "_apply_capability_model_routing"})


def _definitions(tree: ast.Module) -> dict[str, list[int]]:
    """Map each module-level bound name to the lines it is bound on."""

    bound: dict[str, list[int]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.setdefault(node.name, []).append(node.lineno)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.setdefault(target.id, []).append(node.lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.setdefault(node.target.id, []).append(node.lineno)
    return bound


def _module_level_import_lines(tree: ast.Module) -> list[int]:
    return [node.lineno for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]


def _first_definition_line(tree: ast.Module) -> int | None:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return node.lineno
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            return node.lineno
    return None


def test_decomposer_imports_precede_every_definition():
    tree = ast.parse(DECOMPOSER.read_text(encoding="utf-8"))
    imports = _module_level_import_lines(tree)
    first_definition = _first_definition_line(tree)
    assert imports, "the module-level imports were not found"
    assert first_definition is not None, "the module defines nothing at module level"
    # A mid-file import (the old ``# noqa: E402`` shape) would put an import
    # line after the first top-level definition.
    assert max(imports) < first_definition, f"a module-level import sits below line {first_definition}"


def test_decomposer_has_one_definition_per_symbol():
    tree = ast.parse(DECOMPOSER.read_text(encoding="utf-8"))
    duplicated = {name: lines for name, lines in _definitions(tree).items() if len(lines) > 1}
    # Nothing outside the recorded quarantine defect may be shadowed.
    assert set(duplicated) <= KNOWN_SHADOWED, f"newly shadowed top-level definitions: {sorted(set(duplicated) - KNOWN_SHADOWED)}"
    # The recorded defect is still documented, not silently fixed here.
    for name in KNOWN_SHADOWED:
        assert name in duplicated, f"{name!r} was de-duplicated; update KNOWN_SHADOWED and the plan record together"


def test_decomposer_has_no_mid_file_import_silencer():
    source = DECOMPOSER.read_text(encoding="utf-8")
    assert "noqa: E402" not in source, "a module-level import is once again silenced with E402"


def test_decompose_stamps_the_source_of_the_plan():
    """Every plan says how it was decomposed: with no mode opted in, the template says so."""

    plan = SwarmTaskDecomposer.decompose(goal="Implement a sql migration and audit the backend service")
    assert plan.metrics["decomposition"] == {"source": "template", "reason": "decomposition_mode=template"}
