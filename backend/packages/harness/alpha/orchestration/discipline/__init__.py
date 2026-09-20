"""Multi-Model Discipline Agent Team Package (OmO / Sisyphus Engine)."""

from alpha.orchestration.discipline.consultant import GapAnalysisReport, PlanConsultant
from alpha.orchestration.discipline.recon import FastReconWorker, ReconResult
from alpha.orchestration.discipline.reviewer import PlanReviewer, ReviewVerdict, VerdictType
from alpha.orchestration.discipline.team_dispatcher import CategoryTeamDispatcher
from alpha.orchestration.discipline.ultrabrain import UltrabrainSolution, UltrabrainWorker
from alpha.orchestration.discipline.visual_engineering import (
    VisualEngineeringWorker,
    VisualWidgetSpec,
)

__all__ = [
    # Consultant (Claude Fable 5.1)
    "PlanConsultant",
    "GapAnalysisReport",
    # Reviewer (OpenAI GPT-6 Astra)
    "PlanReviewer",
    "ReviewVerdict",
    "VerdictType",
    # Visual Engineering (Claude Fable 5.1)
    "VisualEngineeringWorker",
    "VisualWidgetSpec",
    # Ultrabrain (OpenAI GPT-6 Astra)
    "UltrabrainWorker",
    "UltrabrainSolution",
    # Fast Recon (Explore / Librarian)
    "FastReconWorker",
    "ReconResult",
    # Team Dispatcher
    "CategoryTeamDispatcher",
]
