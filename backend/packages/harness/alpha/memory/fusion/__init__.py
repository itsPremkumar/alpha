"""Memory retrieval fusion and typed context composition.

This package implements the multi-stage read and context-composition contracts
from *ALPHA_ADVANCED_OPEN_SOURCE_AGENTIC_MEMORY_SYSTEM* sections 11 and 12:
exact/semantic/graph/temporal/procedural candidates, disclosed weighted or RRF
fusion, MMR diversity, and per-memory-type token budgets. It is provider-neutral
and default-off; the host owns activation, provider adapters, and prompt wiring.

Exports are lazy so importing :class:`FusionConfig` cannot create a config or
provider cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "BUDGET_PROFILES": "config",
    "BUDGET_TYPE_ORDER": "config",
    "DEFAULT_FUSION_WEIGHTS": "config",
    "BudgetProfile": "config",
    "FusionConfig": "config",
    "FusionStrategy": "config",
    "allocate_type_budgets": "config",
    "fusion_enabled": "config",
    "Candidate": "models",
    "ComposedContext": "models",
    "ContextBlock": "models",
    "DroppedItem": "models",
    "EvidenceGroup": "models",
    "FusedCandidate": "models",
    "FusionResult": "models",
    "GraphBatch": "models",
    "ProvenanceWriteResult": "models",
    "RetrievalMode": "models",
    "StageResult": "models",
    "StageStatus": "models",
    "TemporalOperation": "models",
    "ModeSelection": "query",
    "TaskContext": "query",
    "select_mode": "query",
    "CandidateProvider": "stages",
    "ExactStage": "stages",
    "GraphProvider": "stages",
    "GraphStage": "stages",
    "MAX_GRAPH_DEPTH": "stages",
    "ProceduralStage": "stages",
    "RetrievalStage": "stages",
    "SemanticStage": "stages",
    "TemporalStage": "stages",
    "run_retrieval_stages": "stages",
    "DEFAULT_RRF_K": "fusion",
    "MemoryFusion": "fusion",
    "STAGE_ORDER": "fusion",
    "calculate_weighted_score": "fusion",
    "fuse_stage_results": "fusion",
    "merge_candidates": "fusion",
    "DEFAULT_MMR_LAMBDA": "diversity",
    "DiversityResult": "diversity",
    "NEAR_DUPLICATE_THRESHOLD": "diversity",
    "content_similarity": "diversity",
    "select_diverse": "diversity",
    "TokenEstimator": "composer",
    "compose_context": "composer",
    "deterministic_token_estimate": "composer",
    "query_hash": "provenance",
    "read_recall_provenance": "provenance",
    "recall_provenance_path": "provenance",
    "write_recall_provenance": "provenance",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from .composer import TokenEstimator as TokenEstimator
    from .composer import compose_context as compose_context
    from .composer import deterministic_token_estimate as deterministic_token_estimate
    from .config import BUDGET_PROFILES as BUDGET_PROFILES
    from .config import BUDGET_TYPE_ORDER as BUDGET_TYPE_ORDER
    from .config import DEFAULT_FUSION_WEIGHTS as DEFAULT_FUSION_WEIGHTS
    from .config import BudgetProfile as BudgetProfile
    from .config import FusionConfig as FusionConfig
    from .config import FusionStrategy as FusionStrategy
    from .config import allocate_type_budgets as allocate_type_budgets
    from .config import fusion_enabled as fusion_enabled
    from .diversity import DEFAULT_MMR_LAMBDA as DEFAULT_MMR_LAMBDA
    from .diversity import NEAR_DUPLICATE_THRESHOLD as NEAR_DUPLICATE_THRESHOLD
    from .diversity import DiversityResult as DiversityResult
    from .diversity import content_similarity as content_similarity
    from .diversity import select_diverse as select_diverse
    from .fusion import DEFAULT_RRF_K as DEFAULT_RRF_K
    from .fusion import STAGE_ORDER as STAGE_ORDER
    from .fusion import MemoryFusion as MemoryFusion
    from .fusion import calculate_weighted_score as calculate_weighted_score
    from .fusion import fuse_stage_results as fuse_stage_results
    from .fusion import merge_candidates as merge_candidates
    from .models import Candidate as Candidate
    from .models import ComposedContext as ComposedContext
    from .models import ContextBlock as ContextBlock
    from .models import DroppedItem as DroppedItem
    from .models import EvidenceGroup as EvidenceGroup
    from .models import FusedCandidate as FusedCandidate
    from .models import FusionResult as FusionResult
    from .models import GraphBatch as GraphBatch
    from .models import ProvenanceWriteResult as ProvenanceWriteResult
    from .models import RetrievalMode as RetrievalMode
    from .models import StageResult as StageResult
    from .models import StageStatus as StageStatus
    from .models import TemporalOperation as TemporalOperation
    from .provenance import query_hash as query_hash
    from .provenance import read_recall_provenance as read_recall_provenance
    from .provenance import recall_provenance_path as recall_provenance_path
    from .provenance import write_recall_provenance as write_recall_provenance
    from .query import ModeSelection as ModeSelection
    from .query import TaskContext as TaskContext
    from .query import select_mode as select_mode
    from .stages import MAX_GRAPH_DEPTH as MAX_GRAPH_DEPTH
    from .stages import CandidateProvider as CandidateProvider
    from .stages import ExactStage as ExactStage
    from .stages import GraphProvider as GraphProvider
    from .stages import GraphStage as GraphStage
    from .stages import ProceduralStage as ProceduralStage
    from .stages import RetrievalStage as RetrievalStage
    from .stages import SemanticStage as SemanticStage
    from .stages import TemporalStage as TemporalStage
    from .stages import run_retrieval_stages as run_retrieval_stages

install_lazy_exports(__name__, _EXPORTS)
