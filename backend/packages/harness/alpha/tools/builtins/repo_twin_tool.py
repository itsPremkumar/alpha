"""Built-in repo_twin tool inspired by hermes-agi-asi-harness."""

from __future__ import annotations

import json
from pathlib import Path

from langchain.tools import tool

from alpha.coding.repo_twin import (
    BlastRadiusCalculator,
    RepoRecon,
    SymbolGraph,
)


@tool("inspect_repo_twin", parse_docstring=True)
def inspect_repo_twin(
    root_path: str = ".",
    target_file_for_blast_radius: str = "",
) -> str:
    """Inspect repository digital twin, AST symbol dependencies, and blast radius.

    Discovers build systems (pip, npm, cargo), test frameworks (pytest, jest), CI/CD workflows,
    and calculates blast-radius impact before file modifications.

    Args:
        root_path: Path to repository root (defaults to current directory).
        target_file_for_blast_radius: Optional file path to compute blast-radius and dependent callers.
    """
    root = Path(root_path)
    recon = RepoRecon.scan_workspace(root)

    graph = SymbolGraph()
    for py_file in root.glob("**/*.py"):
        if "venv" in str(py_file) or ".git" in str(py_file):
            continue
        graph.parse_file(py_file)

    blast_report = None
    if target_file_for_blast_radius:
        blast_report = BlastRadiusCalculator.calculate(target_file_for_blast_radius, graph).to_dict()

    return json.dumps({
        "reconnaissance": recon.to_dict(),
        "total_symbols_indexed": len(graph.symbols),
        "blast_radius": blast_report,
    }, indent=2)
