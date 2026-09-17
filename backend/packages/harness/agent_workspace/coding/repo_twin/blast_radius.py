from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .symbol_graph import SymbolGraph


@dataclass
class BlastRadiusReport:
    target_file: str
    target_symbols: list[str] = field(default_factory=list)
    importing_files: list[str] = field(default_factory=list)
    blast_score: float = 0.0  # 0.0 to 1.0 (1.0 = massive blast radius)
    affected_components_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_file": self.target_file,
            "target_symbols": self.target_symbols,
            "importing_files": self.importing_files,
            "blast_score": self.blast_score,
            "affected_components_count": self.affected_components_count,
        }


class BlastRadiusCalculator:
    """Calculates blast radius and impacted files before committing mutations."""

    @staticmethod
    def calculate(target_file: str | Path, graph: SymbolGraph) -> BlastRadiusReport:
        norm_target = str(Path(target_file))
        target_stem = Path(target_file).stem
        target_symbols = [s.name for s in graph.get_symbols_in_file(norm_target)]

        importing_files: set[str] = set()
        for fpath, imports in graph.import_edges.items():
            if fpath == norm_target:
                continue
            for imp in imports:
                if target_stem in imp or imp.endswith(f".{target_stem}"):
                    importing_files.add(fpath)

        # Compute blast score: higher if more files import this target
        total_files = max(1, len(graph.file_symbols))
        score = min(1.0, len(importing_files) / float(total_files))

        return BlastRadiusReport(
            target_file=norm_target,
            target_symbols=target_symbols,
            importing_files=sorted(list(importing_files)),
            blast_score=round(score, 3),
            affected_components_count=len(importing_files) + len(target_symbols),
        )
