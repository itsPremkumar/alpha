import tempfile
from pathlib import Path

from alpha.coding.repo_twin import (
    BlastRadiusCalculator,
    RepoRecon,
    SymbolGraph,
    SymbolType,
)


def test_symbol_graph_ast_parsing():
    code = '''
"""Module docstring."""
import math
from typing import List, Optional

class DataPipeline:
    """Manages pipeline execution."""
    def run(self, items: List[int]) -> int:
        return sum(items)

def helper_util(x: int) -> int:
    return x * 2
'''
    graph = SymbolGraph()
    graph.parse_code("pipeline.py", code)

    symbols = graph.get_symbols_in_file("pipeline.py")
    assert len(symbols) == 3

    class_syms = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
    assert len(class_syms) == 1
    assert class_syms[0].name == "DataPipeline"
    assert "Manages pipeline execution." in class_syms[0].docstring

    fn_syms = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]
    assert len(fn_syms) == 2
    fn_names = [s.name for s in fn_syms]
    assert "run" in fn_names
    assert "helper_util" in fn_names


def test_repo_recon():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "pyproject.toml").write_text("[tool.poetry]\nname='test'", encoding="utf-8")
        (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (root / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('hello')", encoding="utf-8")

        recon = RepoRecon.scan_workspace(root)
        assert "python/pip/pyproject" in recon.build_systems
        assert "pytest" in recon.test_frameworks
        assert "docker" in recon.ci_cd_systems
        assert "python" in recon.languages
        assert recon.total_files_scanned >= 4


def test_blast_radius_calculator():
    graph = SymbolGraph()
    core_code = "def core_engine(): pass\n"
    app_code = "from core import core_engine\ndef main(): core_engine()\n"

    graph.parse_code("core.py", core_code)
    graph.parse_code("app.py", app_code)

    report = BlastRadiusCalculator.calculate("core.py", graph)
    assert report.target_file == str(Path("core.py"))
    assert "app.py" in [str(Path(f)) for f in report.importing_files]
    assert report.blast_score > 0.0
