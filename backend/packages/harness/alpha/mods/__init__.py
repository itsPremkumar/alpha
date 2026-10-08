"""Alpha Mod Kernel (AMK) — autonomous execution governance spine and event middleware."""

from __future__ import annotations

from alpha.mods.cli import main as mod_cli_main
from alpha.mods.context import (
    CapabilityContext,
    ClockCapability,
    EstopCapability,
    EvidenceCapability,
    ModelCapability,
    StorageCapability,
    ToolCapability,
    UiCapability,
)
from alpha.mods.controllers import (
    BotMission,
    BotModeMod,
    FailureRecord,
    FailureSentinelMod,
    TaskRouterMod,
)
from alpha.mods.enforcers import (
    BlastRadiusGuardMod,
    FleetEstopMod,
    RiskLevel,
    VerificationEvidenceGateMod,
)
from alpha.mods.kernel import (
    ModAdmissionError,
    ModKernel,
    get_mod_kernel,
    register_autonomous_controllers,
    require_mod_admission,
    reset_mod_kernel,
    set_mod_kernel,
    sync_dispatch,
)
from alpha.mods.middleware import ModKernelMiddleware
from alpha.mods.types import (
    AlphaEvent,
    AlphaMod,
    CorrelationContext,
    EventOutcome,
    EventResult,
    ModPriority,
    NextHandler,
)

__all__ = [
    # Core types
    "AlphaEvent",
    "AlphaMod",
    "CorrelationContext",
    "EventOutcome",
    "EventResult",
    "ModPriority",
    "NextHandler",
    # Kernel engine
    "ModAdmissionError",
    "ModKernel",
    "get_mod_kernel",
    "register_autonomous_controllers",
    "require_mod_admission",
    "reset_mod_kernel",
    "set_mod_kernel",
    "sync_dispatch",
    # Capability context ($)
    "CapabilityContext",
    "ClockCapability",
    "EstopCapability",
    "EvidenceCapability",
    "ModelCapability",
    "StorageCapability",
    "ToolCapability",
    "UiCapability",
    # Core triad enforcers
    "BlastRadiusGuardMod",
    "FleetEstopMod",
    "RiskLevel",
    "VerificationEvidenceGateMod",
    # Autonomous controllers & sentinels
    "BotMission",
    "BotModeMod",
    "FailureRecord",
    "FailureSentinelMod",
    "TaskRouterMod",
    # Middleware
    "ModKernelMiddleware",
    # CLI
    "mod_cli_main",
]
