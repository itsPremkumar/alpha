"""Symbol Dependency Graph representing DEFINES, CALLS, IMPORTS, and INHERITS relations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from agent_workspace.coding.structural_intelligence.polyglot_cst import (
    PolyglotCSTParser,
    SymbolKind,
    SymbolNode,
)


class EdgeType(str, Enum):
    DEFINES = "defines"
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"


@dataclass
class GraphEdge:
    source_id: str
    target_id: str
    edge_type: EdgeType
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type.value,
            "metadata": self.metadata,
        }


class SymbolDependencyGraph:
    """Directed multi-graph mapping symbols and relations across polyglot repositories."""

    def __init__(self) -> None:
        self.nodes: dict[str, SymbolNode] = {}
        self.edges: list[GraphEdge] = []
        self._adj_out: dict[str, list[GraphEdge]] = {}
        self._adj_in: dict[str, list[GraphEdge]] = {}
        self._name_index: dict[str, list[str]] = {}
        self._file_symbols: dict[str, list[str]] = {}
        self._parser = PolyglotCSTParser()

    def add_node(self, node: SymbolNode) -> None:
        self.nodes[node.id] = node
        self._name_index.setdefault(node.name, []).append(node.id)
        self._file_symbols.setdefault(node.file_path, []).append(node.id)

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        edge = GraphEdge(
            source_id=source_id,
            target_id=target_id,
            edge_type=edge_type,
            metadata=metadata or {},
        )
        self.edges.append(edge)
        self._adj_out.setdefault(source_id, []).append(edge)
        self._adj_in.setdefault(target_id, []).append(edge)

    def parse_and_add_file(self, file_path: str, code: str) -> None:
        """Parses a file's code and updates nodes and edges in the graph."""
        parsed = self._parser.parse_source(file_path, code)
        norm_file = parsed.file_path

        # Add file node if not already present
        if norm_file not in self.nodes:
            file_node = SymbolNode(
                id=norm_file,
                name=Path(norm_file).name,
                kind=SymbolKind.FILE,
                file_path=norm_file,
                start_line=1,
                end_line=len(code.splitlines()) or 1,
            )
            self.add_node(file_node)

        # Add symbol nodes
        for sym in parsed.symbols:
            self.add_node(sym)

        # DEFINES
        for src, dst in parsed.defines_edges:
            self.add_edge(src, dst, EdgeType.DEFINES)

        # IMPORTS
        for file_p, imp in parsed.imports_edges:
            self.add_edge(file_p, imp, EdgeType.IMPORTS)

        # INHERITS
        for subclass_id, base_name in parsed.inherits_edges:
            # Resolve base class if known symbol or target by name
            matching = self.find_symbol_by_name(base_name)
            target = matching[0].id if matching else base_name
            self.add_edge(subclass_id, target, EdgeType.INHERITS)

        # CALLS: Resolve called symbols by name where possible
        for caller_id, callee_name in parsed.calls_edges:
            matching = self.find_symbol_by_name(callee_name)
            target = matching[0].id if matching else callee_name
            self.add_edge(caller_id, target, EdgeType.CALLS)

    def parse_directory(self, root_dir: str | Path, max_files: int = 500) -> None:
        """Walks directory and indexes code files into the symbol graph."""
        p = Path(root_dir)
        if not p.is_dir():
            return

        count = 0
        for ext in PolyglotCSTParser.SUPPORTED_EXTENSIONS:
            for file_p in p.rglob(f"*{ext}"):
                if "node_modules" in file_p.parts or ".git" in file_p.parts or "__pycache__" in file_p.parts or ".venv" in file_p.parts:
                    continue
                try:
                    code = file_p.read_text(encoding="utf-8", errors="replace")
                    rel_path = str(file_p.relative_to(p)).replace("\\", "/")
                    self.parse_and_add_file(rel_path, code)
                    count += 1
                    if count >= max_files:
                        return
                except Exception:
                    continue

    def get_symbols_in_file(self, file_path: str) -> list[SymbolNode]:
        norm = str(Path(file_path)).replace("\\", "/")
        ids = self._file_symbols.get(norm, [])
        return [self.nodes[sid] for sid in ids if sid in self.nodes]

    def find_symbol_by_name(self, name: str) -> list[SymbolNode]:
        ids = self._name_index.get(name, [])
        return [self.nodes[sid] for sid in ids if sid in self.nodes]

    def get_callers(self, symbol_id: str) -> list[str]:
        """Returns ids of symbols that call symbol_id."""
        incoming = self._adj_in.get(symbol_id, [])
        return [e.source_id for e in incoming if e.edge_type == EdgeType.CALLS]

    def get_callees(self, symbol_id: str) -> list[str]:
        """Returns ids or names of functions/methods called by symbol_id."""
        outgoing = self._adj_out.get(symbol_id, [])
        return [e.target_id for e in outgoing if e.edge_type == EdgeType.CALLS]

    def get_inheritors(self, class_id: str) -> list[str]:
        """Returns ids of classes that subclass/implement class_id."""
        incoming = self._adj_in.get(class_id, [])
        return [e.source_id for e in incoming if e.edge_type == EdgeType.INHERITS]

    def get_bases(self, class_id: str) -> list[str]:
        """Returns base classes inherited by class_id."""
        outgoing = self._adj_out.get(class_id, [])
        return [e.target_id for e in outgoing if e.edge_type == EdgeType.INHERITS]

    def get_imports_for_file(self, file_path: str) -> list[str]:
        norm = str(Path(file_path)).replace("\\", "/")
        outgoing = self._adj_out.get(norm, [])
        return [e.target_id for e in outgoing if e.edge_type == EdgeType.IMPORTS]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
        }
