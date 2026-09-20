"""Personalized PageRank (PPR) compact repository map generator.

Packs repository structural outlines into strict token budgets (e.g. 1,500 tokens)
biased by active task query and open/active files.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

from alpha.coding.structural_intelligence.polyglot_cst import SymbolKind, SymbolNode
from alpha.coding.structural_intelligence.symbol_dependency_graph import (
    EdgeType,
    SymbolDependencyGraph,
)


class PersonalizedPageRank:
    """Computes Personalized PageRank over the Symbol Dependency Graph."""

    def __init__(self, damping: float = 0.85, max_iter: int = 40, tol: float = 1e-6) -> None:
        self.damping = damping
        self.max_iter = max_iter
        self.tol = tol

    def compute(
        self,
        graph: SymbolDependencyGraph,
        seed_nodes: list[str],
        seed_weights: Optional[dict[str, float]] = None,
    ) -> dict[str, float]:
        """Computes Personalized PageRank biased toward seed_nodes.
        
        Args:
            graph: The symbol dependency graph.
            seed_nodes: Node IDs (files or symbols) representing active focus/query.
            seed_weights: Optional relative weights for seeds.
        """
        all_nodes = list(graph.nodes.keys())
        # Also include any edge targets that might not be in graph.nodes
        node_set = set(all_nodes)
        for edge in graph.edges:
            node_set.add(edge.source_id)
            node_set.add(edge.target_id)
        
        nodes = sorted(list(node_set))
        n = len(nodes)
        if n == 0:
            return {}

        node_index = {node: i for i, node in enumerate(nodes)}

        # Build adjacency list: node -> outgoing neighbors
        adj: list[list[int]] = [[] for _ in range(n)]
        for edge in graph.edges:
            src_idx = node_index[edge.source_id]
            dst_idx = node_index[edge.target_id]
            adj[src_idx].append(dst_idx)

        # Build personalization vector v
        v = [0.0] * n
        seed_weight_map: dict[str, float] = {}
        for s in seed_nodes:
            if s in node_index:
                base_w = seed_weights.get(s, 1.0) if seed_weights else 1.0
                seed_weight_map[s] = seed_weight_map.get(s, 0.0) + base_w

        if seed_weight_map:
            total_w = sum(seed_weight_map.values())
            for s, w in seed_weight_map.items():
                idx = node_index[s]
                v[idx] = w / (total_w or 1.0)
        else:
            # Uniform fallback
            uniform_val = 1.0 / n
            v = [uniform_val] * n

        # Initial rank vector r
        r = [1.0 / n] * n

        # Power iteration
        d = self.damping
        for _ in range(self.max_iter):
            next_r = [(1.0 - d) * v[i] for i in range(n)]
            dangling_sum = 0.0

            for i in range(n):
                out_deg = len(adj[i])
                if out_deg > 0:
                    share = d * r[i] / out_deg
                    for dst in adj[i]:
                        next_r[dst] += share
                else:
                    dangling_sum += r[i]

            # Redistribute dangling mass
            if dangling_sum > 0.0:
                for i in range(n):
                    next_r[i] += d * dangling_sum * v[i]

            # Convergence check (L1 norm)
            diff = sum(abs(next_r[i] - r[i]) for i in range(n))
            r = next_r
            if diff < self.tol:
                break

        return {nodes[i]: r[i] for i in range(n)}


class RepoMapGenerator:
    """Ranks and formats repository outlines to fit within context token budgets."""

    def __init__(
        self,
        token_budget: int = 1500,
        char_per_token: float = 4.0,
        ppr_engine: Optional[PersonalizedPageRank] = None,
    ) -> None:
        self.token_budget = token_budget
        self.char_per_token = char_per_token
        self.char_budget = int(token_budget * char_per_token)
        self.ppr = ppr_engine or PersonalizedPageRank()

    def generate_repo_map(
        self,
        graph: SymbolDependencyGraph,
        active_files: Optional[list[str]] = None,
        query: Optional[str] = None,
    ) -> str:
        """Generates a compact structural repo map biased by active files and query."""
        seed_nodes: list[str] = []
        active_files = active_files or []

        # 1. Normalize active files into seeds
        for f in active_files:
            norm_f = str(Path(f)).replace("\\", "/")
            if norm_f in graph.nodes:
                seed_nodes.append(norm_f)
            # Add symbols in the active file as high-priority seeds
            for sym in graph.get_symbols_in_file(norm_f):
                seed_nodes.append(sym.id)

        # 2. Match query tokens to symbol names
        if query:
            tokens = [t.lower() for t in query.replace("_", " ").split() if len(t) > 2]
            for name, ids in graph._name_index.items():
                if any(t in name.lower() for t in tokens):
                    seed_nodes.extend(ids)

        # 3. Compute PPR
        scores = self.ppr.compute(graph, seed_nodes)

        # 4. Group symbols by file and calculate file score
        file_scores: dict[str, float] = {}
        for nid, node in graph.nodes.items():
            if node.kind == SymbolKind.FILE:
                file_scores.setdefault(node.file_path, scores.get(nid, 0.0))

        for nid, score in scores.items():
            if nid in graph.nodes:
                node = graph.nodes[nid]
                file_path = node.file_path
                file_scores[file_path] = file_scores.get(file_path, 0.0) + score

        # Rank files by score
        ranked_files = sorted(file_scores.keys(), key=lambda f: file_scores[f], reverse=True)

        # 5. Pack into token budget
        output_lines: list[str] = ["# Repository Structural Map (Context Outline)"]
        current_chars = len(output_lines[0])

        for file_p in ranked_files:
            symbols = graph.get_symbols_in_file(file_p)
            # Sort symbols in file by individual PPR score or line order
            symbols_sorted = sorted(
                symbols,
                key=lambda s: scores.get(s.id, 0.0),
                reverse=True,
            )

            file_header = f"\n{file_p}:"
            if current_chars + len(file_header) > self.char_budget:
                break

            file_block = [file_header]
            added_syms = 0

            for sym in symbols_sorted:
                sig = self._format_symbol_signature(sym)
                line_entry = f"  {sig}"
                if current_chars + len("\n".join(file_block)) + len(line_entry) > self.char_budget:
                    break
                file_block.append(line_entry)
                added_syms += 1

            if added_syms > 0 or len(symbols) == 0:
                block_str = "\n".join(file_block)
                output_lines.append(block_str)
                current_chars += len(block_str)

        return "\n".join(output_lines)

    def _format_symbol_signature(self, sym: SymbolNode) -> str:
        kind_prefix = {
            SymbolKind.CLASS: "class",
            SymbolKind.INTERFACE: "interface",
            SymbolKind.STRUCT: "struct",
            SymbolKind.TRAIT: "trait",
            SymbolKind.FUNCTION: "def",
            SymbolKind.METHOD: "def",
        }.get(sym.kind, "def")

        params_str = ", ".join(sym.parameters) if sym.parameters else ""
        ret_str = f" -> {sym.return_type}" if sym.return_type else ""
        doc_summary = f" # {sym.docstring.splitlines()[0]}" if sym.docstring else ""

        modifiers = " ".join(sym.modifiers) + " " if sym.modifiers else ""
        return f"{modifiers}{kind_prefix} {sym.name}({params_str}){ret_str}{doc_summary}"
