"""Lazy public surface for codebase structure memory.

The concrete modules are intentionally not imported at package import time.
This follows the repository's memory-package convention and lets an embedder
read :class:`CodebaseConfig` without importing graph, storage, or recall code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "ChangeImpact": "models",
    "CodebaseConfig": "config",
    "CodebaseGraph": "graph",
    "CodebaseIndexer": "indexer",
    "CodebaseRecall": "recall",
    "CodebaseRecaller": "recall",
    "CodebaseRefresh": "refresh",
    "CodebaseSnapshot": "models",
    "CodebaseStore": "store",
    "CycleError": "graph",
    "DependencyEdge": "models",
    "DependencyGraph": "graph",
    "FileDescriptor": "indexer",
    "FileEntry": "indexer",
    "Hotspot": "models",
    "HotspotScorer": "hotspots",
    "ImpactAnalyzer": "impact",
    "ModuleRecord": "models",
    "RefreshCoordinator": "refresh",
    "RefreshReport": "models",
    "SkippedFile": "models",
    "SymbolRef": "models",
    "atomic_write_text": "paths",
    "calculate_hotspots": "hotspots",
    "compute_hotspots": "hotspots",
    "change_impact": "impact",
    "codebase_root": "paths",
    "compute_change_impact": "impact",
    "compute_impact": "impact",
    "get_codebase_config": "config",
    "impact_block": "recall",
    "is_test_path": "impact",
    "l1_root": "paths",
    "module_summary_block": "recall",
    "recall_blocks": "recall",
    "repo_dir": "paths",
    "safe_segment": "paths",
    "score_hotspots": "hotspots",
    "snapshot_path": "paths",
    "structure_block": "recall",
    "symbols_block": "recall",
    "refresh_snapshot": "refresh",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from alpha.memory.codebase.config import CodebaseConfig as CodebaseConfig
    from alpha.memory.codebase.config import get_codebase_config as get_codebase_config
    from alpha.memory.codebase.graph import CodebaseGraph as CodebaseGraph
    from alpha.memory.codebase.graph import CycleError as CycleError
    from alpha.memory.codebase.graph import DependencyGraph as DependencyGraph
    from alpha.memory.codebase.hotspots import HotspotScorer as HotspotScorer
    from alpha.memory.codebase.hotspots import calculate_hotspots as calculate_hotspots
    from alpha.memory.codebase.hotspots import compute_hotspots as compute_hotspots
    from alpha.memory.codebase.hotspots import score_hotspots as score_hotspots
    from alpha.memory.codebase.impact import ImpactAnalyzer as ImpactAnalyzer
    from alpha.memory.codebase.impact import change_impact as change_impact
    from alpha.memory.codebase.impact import compute_change_impact as compute_change_impact
    from alpha.memory.codebase.impact import compute_impact as compute_impact
    from alpha.memory.codebase.impact import is_test_path as is_test_path
    from alpha.memory.codebase.indexer import CodebaseIndexer as CodebaseIndexer
    from alpha.memory.codebase.indexer import FileDescriptor as FileDescriptor
    from alpha.memory.codebase.indexer import FileEntry as FileEntry
    from alpha.memory.codebase.models import ChangeImpact as ChangeImpact
    from alpha.memory.codebase.models import CodebaseSnapshot as CodebaseSnapshot
    from alpha.memory.codebase.models import DependencyEdge as DependencyEdge
    from alpha.memory.codebase.models import Hotspot as Hotspot
    from alpha.memory.codebase.models import ModuleRecord as ModuleRecord
    from alpha.memory.codebase.models import RefreshReport as RefreshReport
    from alpha.memory.codebase.models import SkippedFile as SkippedFile
    from alpha.memory.codebase.models import SymbolRef as SymbolRef
    from alpha.memory.codebase.paths import atomic_write_text as atomic_write_text
    from alpha.memory.codebase.paths import codebase_root as codebase_root
    from alpha.memory.codebase.paths import l1_root as l1_root
    from alpha.memory.codebase.paths import repo_dir as repo_dir
    from alpha.memory.codebase.paths import safe_segment as safe_segment
    from alpha.memory.codebase.paths import snapshot_path as snapshot_path
    from alpha.memory.codebase.recall import CodebaseRecall as CodebaseRecall
    from alpha.memory.codebase.recall import CodebaseRecaller as CodebaseRecaller
    from alpha.memory.codebase.recall import impact_block as impact_block
    from alpha.memory.codebase.recall import module_summary_block as module_summary_block
    from alpha.memory.codebase.recall import recall_blocks as recall_blocks
    from alpha.memory.codebase.recall import structure_block as structure_block
    from alpha.memory.codebase.recall import symbols_block as symbols_block
    from alpha.memory.codebase.refresh import CodebaseRefresh as CodebaseRefresh
    from alpha.memory.codebase.refresh import RefreshCoordinator as RefreshCoordinator
    from alpha.memory.codebase.refresh import refresh_snapshot as refresh_snapshot
    from alpha.memory.codebase.store import CodebaseStore as CodebaseStore

install_lazy_exports(__name__, _EXPORTS, public=tuple(_EXPORTS))
