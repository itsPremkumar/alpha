from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SymbolType(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    VARIABLE = "variable"
    IMPORT = "import"


class EdgeType(str, Enum):
    DEFINES = "defines"
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"


@dataclass
class SymbolNode:
    name: str
    symbol_type: SymbolType
    file_path: str
    line_number: int
    docstring: str = ""
    parameters: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "symbol_type": self.symbol_type.value,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "docstring": self.docstring,
            "parameters": self.parameters,
        }


class SymbolGraph:
    """AST-based symbol and dependency graph for a codebase."""

    def __init__(self) -> None:
        self.symbols: dict[str, SymbolNode] = {}  # key: file_path:name
        self.file_symbols: dict[str, list[str]] = {}  # file_path -> [symbol_keys]
        self.call_edges: dict[str, set[str]] = {}  # caller_key -> set of callee_keys
        self.import_edges: dict[str, set[str]] = {}  # file_path -> set of imported module names

    def parse_code(self, file_path: str, code: str) -> None:
        """Parses Python source code into the symbol graph using ast."""
        try:
            tree = ast.parse(code, filename=file_path)
        except Exception:
            return  # Skip syntax errors in raw file

        norm_path = str(Path(file_path))
        self.file_symbols[norm_path] = []
        self.import_edges[norm_path] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.import_edges[norm_path].add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self.import_edges[norm_path].add(node.module)
            elif isinstance(node, ast.ClassDef):
                key = f"{norm_path}:{node.name}"
                doc = ast.get_docstring(node) or ""
                sym = SymbolNode(
                    name=node.name,
                    symbol_type=SymbolType.CLASS,
                    file_path=norm_path,
                    line_number=node.lineno,
                    docstring=doc,
                )
                self.symbols[key] = sym
                self.file_symbols[norm_path].append(key)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = f"{norm_path}:{node.name}"
                doc = ast.get_docstring(node) or ""
                params = [arg.arg for arg in node.args.args]
                sym = SymbolNode(
                    name=node.name,
                    symbol_type=SymbolType.FUNCTION,
                    file_path=norm_path,
                    line_number=node.lineno,
                    docstring=doc,
                    parameters=params,
                )
                self.symbols[key] = sym
                self.file_symbols[norm_path].append(key)

    def parse_file(self, file_path: str | Path) -> None:
        p = Path(file_path)
        if p.is_file() and p.suffix == ".py":
            try:
                code = p.read_text(encoding="utf-8", errors="replace")
                self.parse_code(str(p), code)
            except Exception:
                pass

    def get_symbols_in_file(self, file_path: str) -> list[SymbolNode]:
        norm_path = str(Path(file_path))
        keys = self.file_symbols.get(norm_path, [])
        return [self.symbols[k] for k in keys if k in self.symbols]

    def find_symbol_by_name(self, name: str) -> list[SymbolNode]:
        return [sym for sym in self.symbols.values() if sym.name == name]

    def generate_compact_repo_map(
        self,
        active_files: list[str] | None = None,
        query: str | None = None,
        token_budget: int = 1500,
    ) -> str:
        """Generates a compact repo map using Personalized PageRank within token budget."""
        from alpha.coding.structural_intelligence.symbol_dependency_graph import SymbolDependencyGraph
        from alpha.coding.structural_intelligence.repo_map import RepoMapGenerator

        graph = SymbolDependencyGraph()
        for sym in self.symbols.values():
            from alpha.coding.structural_intelligence.polyglot_cst import SymbolKind, SymbolNode as RichNode
            kind_map = {
                SymbolType.CLASS: SymbolKind.CLASS,
                SymbolType.FUNCTION: SymbolKind.FUNCTION,
                SymbolType.VARIABLE: SymbolKind.VARIABLE,
                SymbolType.IMPORT: SymbolKind.IMPORT,
                SymbolType.MODULE: SymbolKind.MODULE,
            }
            node = RichNode(
                id=f"{sym.file_path}:{sym.name}",
                name=sym.name,
                kind=kind_map.get(sym.symbol_type, SymbolKind.FUNCTION),
                file_path=sym.file_path,
                start_line=sym.line_number,
                end_line=sym.line_number,
                docstring=sym.docstring,
                parameters=sym.parameters,
            )
            graph.add_node(node)
        
        generator = RepoMapGenerator(token_budget=token_budget)
        return generator.generate_repo_map(graph, active_files=active_files, query=query)

