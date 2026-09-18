"""Unit tests for Structural 3-Way AST Conflict Reconciler (SWE-EVO)."""

import ast
import pytest

from agent_workspace.editing.structural_ast_reconciler import (
    StructuralAstConflictReconciler,
    reconcile_structural_ast_conflicts,
)


def test_reconcile_imports_deduplication():
    base = "from typing import List\nimport os\n"
    ours = "from typing import List, Dict\nimport os\nimport sys\n"
    theirs = "from typing import List, Optional\nimport os\nimport json\n"

    reconciler = StructuralAstConflictReconciler()
    res = reconciler.reconcile(base_code=base, ours_code=ours, theirs_code=theirs)

    assert res["success"] is True
    merged = res["merged_code"]
    # All imported modules should be present and valid
    assert "import json" in merged
    assert "import os" in merged
    assert "import sys" in merged
    assert "from typing import" in merged
    assert "Dict" in merged and "List" in merged and "Optional" in merged


def test_reconcile_disjoint_function_additions():
    base = "def existing_fn():\n    return 1\n"
    ours = "def existing_fn():\n    return 1\n\ndef helper_ours():\n    return 'ours'\n"
    theirs = "def existing_fn():\n    return 1\n\ndef helper_theirs():\n    return 'theirs'\n"

    reconciler = StructuralAstConflictReconciler()
    res = reconciler.reconcile(base_code=base, ours_code=ours, theirs_code=theirs)

    assert res["success"] is True
    merged = res["merged_code"]
    assert "def existing_fn" in merged
    assert "def helper_ours" in merged
    assert "def helper_theirs" in merged

    # Verifies no git conflict markers exist
    assert "<<<<<<<" not in merged
    assert "=======" not in merged
    assert ">>>>>>>" not in merged

    # Verifies resulting code parses as valid Python AST
    ast.parse(merged)


def test_reconcile_function_body_and_docstring():
    base = """def calculate(x):
    return x * 2
"""
    # Ours adds docstring and type hint
    ours = """def calculate(x: int):
    \"\"\"Compute double of input.\"\"\"
    return x * 2
"""
    # Theirs changes return logic
    theirs = """def calculate(x):
    result = x * 2
    return result
"""

    reconciler = StructuralAstConflictReconciler()
    res = reconciler.reconcile(base_code=base, ours_code=ours, theirs_code=theirs)

    assert res["success"] is True
    merged = res["merged_code"]
    # Docstring preserved from Ours
    assert "Compute double of input" in merged
    # Result statement incorporated
    assert "result = x * 2" in merged or "return result" in merged
    # Must parse without syntax errors
    tree = ast.parse(merged)
    assert len(tree.body) >= 1


def test_reconcile_class_methods():
    base = """class Service:
    def __init__(self):
        self.active = True
"""
    ours = """class Service:
    def __init__(self):
        self.active = True

    def start(self):
        self.active = True
"""
    theirs = """class Service:
    def __init__(self):
        self.active = True

    def stop(self):
        self.active = False
"""

    reconciler = StructuralAstConflictReconciler()
    res = reconciler.reconcile(base_code=base, ours_code=ours, theirs_code=theirs)

    assert res["success"] is True
    merged = res["merged_code"]
    assert "class Service" in merged
    assert "def start" in merged
    assert "def stop" in merged
    ast.parse(merged)


def test_reconcile_structural_ast_conflicts_tool():
    base = "x = 1\n"
    ours = "x = 1\ny = 2\n"
    theirs = "x = 1\nz = 3\n"

    res = reconcile_structural_ast_conflicts.invoke({
        "base_code": base,
        "ours_code": ours,
        "theirs_code": theirs,
        "file_path": "constants.py",
    })

    assert isinstance(res, dict)
    assert res["success"] is True
    assert "merged_code" in res["data"]
    assert "y = 2" in res["data"]["merged_code"]
    assert "z = 3" in res["data"]["merged_code"]
