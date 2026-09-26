"""Core orchestrator additions (OpenClaw 2.0-inspired), additive-only.

This package unifies 15 advanced orchestrator capabilities on top of the
existing Alpha harness modules. It never replaces existing owners:

- context engine -> wraps alpha.context.engine.ContextEngine
- provider routing -> builds on alpha.models.fallback / failover
- approvals -> new custody store, consumed by guardrails/authz
- secrets -> resolves secretRef:// on top of runtime.secret_context
- durable tasks / automations / governor -> wraps runtime.runs + scheduler
- memory recall / dreaming trigger -> wraps memory.dreaming + active_memory
- sessions catalog -> sqlite-backed thread bindings + branch/rewind helpers
- tracing -> re-exports alpha.trace_context with subagent/memory helpers
- acp binding -> thread-scoped ACP agent registry
- restart -> the startup seam that resumes durable work after a crash
"""

from alpha.orchestrator.acp_binding import AcpBindingRegistry, get_acp_registry
from alpha.orchestrator.approvals import ApprovalCustodyStore, ApprovalDecision, get_approval_store
from alpha.orchestrator.automations import AutomationDefinition, AutomationScheduler
from alpha.orchestrator.context_engine_plugin import ContextEnginePlugin, get_context_engine_plugin
from alpha.orchestrator.durable_tasks import (
    ConcurrencyGovernor,
    DedupeCache,
    DeliveryQueue,
    DurableTaskRecord,
    DurableTaskRuntime,
    TokenBudgetGovernor,
)
from alpha.orchestrator.dynamic_service import (
    DynamicDecision,
    DynamicPlan,
    DynamicRequest,
    DynamicServiceResult,
    DynamicWorkflowService,
    get_dynamic_workflow_service,
    run_dynamic_turn,
    set_dynamic_workflow_service,
)
from alpha.orchestrator.memory_recall import CrossThreadRecallConfig, trigger_dream_cycle
from alpha.orchestrator.provider_routing import (
    ChannelModelOverride,
    UtilityModelRouter,
    build_fallback_chain,
)
from alpha.orchestrator.restart import (
    install_restart_hooks,
    register_restart_hook,
    run_restart_hooks,
    run_restart_recovery,
)
from alpha.orchestrator.secrets import is_secret_ref, resolve_secret_refs
from alpha.orchestrator.sessions import SessionCatalog, SessionRecord
from alpha.orchestrator.tracing import bind_trace_for_subagent, propagate_trace_to_memory

__all__ = [
    "AcpBindingRegistry",
    "ApprovalCustodyStore",
    "ApprovalDecision",
    "AutomationDefinition",
    "AutomationScheduler",
    "ChannelModelOverride",
    "ConcurrencyGovernor",
    "ContextEnginePlugin",
    "CrossThreadRecallConfig",
    "DynamicDecision",
    "DynamicPlan",
    "DynamicRequest",
    "DynamicServiceResult",
    "DynamicWorkflowService",
    "DeliveryQueue",
    "DedupeCache",
    "DurableTaskRecord",
    "DurableTaskRuntime",
    "SessionCatalog",
    "SessionRecord",
    "TokenBudgetGovernor",
    "UtilityModelRouter",
    "bind_trace_for_subagent",
    "build_fallback_chain",
    "get_acp_registry",
    "get_approval_store",
    "get_context_engine_plugin",
    "get_dynamic_workflow_service",
    "install_restart_hooks",
    "is_secret_ref",
    "propagate_trace_to_memory",
    "register_restart_hook",
    "resolve_secret_refs",
    "run_dynamic_turn",
    "run_restart_hooks",
    "run_restart_recovery",
    "set_dynamic_workflow_service",
    "trigger_dream_cycle",
]
