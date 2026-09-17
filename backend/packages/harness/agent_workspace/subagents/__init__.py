from .config import SubagentConfig
from .lifecycle import (
    SubagentContract,
    SubagentDeliverable,
    SubagentHeartbeat,
    SubagentLease,
    SubagentLifecycleManager,
    SubagentRecord,
    SubagentStatusEnum,
    get_subagent_lifecycle_manager,
)
from .promotion import (
    SubagentPromotionManager,
    SubagentRoleMetric,
    get_subagent_promotion_manager,
)
from .registry import get_available_subagent_names, get_subagent_config, list_subagents
from .resilience import (
    SubagentCheckpoint,
    SubagentResilienceEngine,
    get_subagent_resilience_engine,
)
from .specialists import (
    SpecialistRoleArchetype,
    SpecialistTemplate,
    generate_dynamic_role,
    get_archetype_template,
)

__all__ = [
    "SubagentConfig",
    "SubagentExecutor",
    "SubagentResult",
    "SubagentRuntime",
    "get_available_subagent_names",
    "get_subagent_config",
    "list_subagents",
    "SubagentContract",
    "SubagentDeliverable",
    "SubagentHeartbeat",
    "SubagentLease",
    "SubagentLifecycleManager",
    "SubagentRecord",
    "SubagentStatusEnum",
    "get_subagent_lifecycle_manager",
    "SubagentCheckpoint",
    "SubagentResilienceEngine",
    "get_subagent_resilience_engine",
    "SubagentPromotionManager",
    "SubagentRoleMetric",
    "get_subagent_promotion_manager",
    "SpecialistRoleArchetype",
    "SpecialistTemplate",
    "generate_dynamic_role",
    "get_archetype_template",
]


def __getattr__(name: str):
    if name in {"SubagentExecutor", "SubagentResult"}:
        from .executor import SubagentExecutor, SubagentResult

        exports = {
            "SubagentExecutor": SubagentExecutor,
            "SubagentResult": SubagentResult,
        }
        globals().update(exports)
        return exports[name]
    if name == "SubagentRuntime":
        from .runtime import SubagentRuntime

        globals()[name] = SubagentRuntime
        return SubagentRuntime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
