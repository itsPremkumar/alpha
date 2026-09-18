"""Core orchestrator additions (OpenClaw 2.0-inspired), additive-only.

This package unifies 15 advanced orchestrator capabilities on top of the
existing Agent Workspace harness modules. It never replaces existing owners:

- context engine -> wraps agent_workspace.context.engine.ContextEngine
- provider routing -> builds on agent_workspace.models.fallback / failover
- approvals -> new custody store, consumed by guardrails/authz
- secrets -> resolves secretRef:// on top of runtime.secret_context
- durable tasks / automations / governor -> wraps runtime.runs + scheduler
- memory recall / dreaming trigger -> wraps memory.dreaming + active_memory
- sessions catalog -> sqlite-backed thread bindings + branch/rewind helpers
- tracing -> re-exports agent_workspace.trace_context with subagent/memory helpers
- acp binding -> thread-scoped ACP agent registry
"""

from agent_workspace.orchestrator.acp_binding import AcpBindingRegistry, get_acp_registry
from agent_workspace.orchestrator.approvals import ApprovalCustodyStore, ApprovalDecision, get_approval_store
from agent_workspace.orchestrator.automations import AutomationDefinition, AutomationScheduler
from agent_workspace.orchestrator.context_engine_plugin import ContextEnginePlugin, get_context_engine_plugin
from agent_workspace.orchestrator.durable_tasks import (
    ConcurrencyGovernor,
    DedupeCache,
    DeliveryQueue,
    DurableTaskRecord,
    DurableTaskRuntime,
    TokenBudgetGovernor,
)
from agent_workspace.orchestrator.memory_recall import CrossThreadRecallConfig, trigger_dream_cycle
from agent_workspace.orchestrator.provider_routing import (
    ChannelModelOverride,
    UtilityModelRouter,
    build_fallback_chain,
)
from agent_workspace.orchestrator.secrets import is_secret_ref, resolve_secret_refs
from agent_workspace.orchestrator.sessions import SessionCatalog, SessionRecord
from agent_workspace.orchestrator.tracing import bind_trace_for_subagent, propagate_trace_to_memory

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
    "is_secret_ref",
    "propagate_trace_to_memory",
    "resolve_secret_refs",
    "trigger_dream_cycle",
]
