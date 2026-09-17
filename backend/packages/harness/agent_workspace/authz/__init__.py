"""Pluggable fine-grained authorization (resource-level RBAC and beyond)."""

from agent_workspace.authz.adapter import GuardrailAuthorizationAdapter
from agent_workspace.authz.enforcement import filter_tools_by_authorization
from agent_workspace.authz.principal import build_principal_from_context, normalize_authz_attributes
from agent_workspace.authz.provider import AuthorizationProvider, AuthzDecision, AuthzReason, AuthzRequest, Principal
from agent_workspace.authz.rbac import RbacAuthorizationProvider
from agent_workspace.authz.runtime import resolve_authorization_provider
from agent_workspace.authz.sandbox_authz import authorize_sandbox_execution
from agent_workspace.authz.tool_filter import apply_tool_authorization

__all__ = [
    "AuthzDecision",
    "AuthzReason",
    "AuthzRequest",
    "AuthorizationProvider",
    "GuardrailAuthorizationAdapter",
    "Principal",
    "RbacAuthorizationProvider",
    "apply_tool_authorization",
    "authorize_sandbox_execution",
    "build_principal_from_context",
    "filter_tools_by_authorization",
    "normalize_authz_attributes",
    "resolve_authorization_provider",
]
