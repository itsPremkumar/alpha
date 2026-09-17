"""Multi-Model Discipline Agent Team Package (OmO / Sisyphus Engine)."""

from agent_workspace.orchestration.discipline.consultant import GapAnalysisReport, PlanConsultant
from agent_workspace.orchestration.discipline.recon import FastReconWorker, ReconResult
from agent_workspace.orchestration.discipline.reviewer import PlanReviewer, ReviewVerdict, VerdictType
from agent_workspace.orchestration.discipline.team_dispatcher import CategoryTeamDispatcher
from agent_workspace.orchestration.discipline.ultrabrain import UltrabrainSolution, UltrabrainWorker
from agent_workspace.orchestration.discipline.visual_engineering import (
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
