"""Adversarial Hyperplan Multi-Reviewer Pipeline.
Inspired by oh-my-openagent (OmO) hyperplan and ulw-loop reviewers.
"""

from agent_workspace.planning.autonomous import (
    AutonomousPlan,
    AutonomousPlanner,
    AutoSubtask,
    NewProfileSpec,
    detect_domains,
    review_wave,
)
from agent_workspace.planning.bridge import (
    AutonomousDispatchBridge,
    DispatchResult,
)
from agent_workspace.planning.hyperplan import (
    HyperplanPipeline,
    HyperplanReport,
    ReviewerVerdict,
)
from agent_workspace.planning.meta_planner import (
    CognitiveMetaPlanner,
    ExecutionParadigm,
    MetaPlan,
    MetaPlanDecision,
    MetaPlanTask,
)
from agent_workspace.planning.profiles import (
    install_profiles,
    profile_spec_to_managed_definition,
    profile_spec_to_subagent_config,
    profile_spec_to_system_prompt,
    sanitize_profile_name,
)

from agent_workspace.planning.integrity import (
    GoalIntegrityEngine,
    GoalIntegrityReport,
)

__all__ = [
    "ReviewerVerdict",
    "HyperplanReport",
    "HyperplanPipeline",
    "AutonomousPlan",
    "AutonomousPlanner",
    "AutoSubtask",
    "NewProfileSpec",
    "detect_domains",
    "review_wave",
    "install_profiles",
    "profile_spec_to_managed_definition",
    "profile_spec_to_subagent_config",
    "profile_spec_to_system_prompt",
    "sanitize_profile_name",
    "CognitiveMetaPlanner",
    "AutonomousDispatchBridge",
    "DispatchResult",
    "ExecutionParadigm",
    "MetaPlan",
    "MetaPlanDecision",
    "MetaPlanTask",
    "GoalIntegrityEngine",
    "GoalIntegrityReport",
]

