"""Pluggable fine-grained authorization (resource-level RBAC and beyond)."""

from alpha.authz.adapter import GuardrailAuthorizationAdapter
from alpha.authz.enforcement import filter_tools_by_authorization
from alpha.authz.principal import build_principal_from_context, normalize_authz_attributes
from alpha.authz.provider import AuthorizationProvider, AuthzDecision, AuthzReason, AuthzRequest, Principal
from alpha.authz.rbac import RbacAuthorizationProvider
from alpha.authz.runtime import resolve_authorization_provider
from alpha.authz.tool_filter import apply_tool_authorization


def __getattr__(name: str):
    """Load sandbox authorization only when its public export is requested.

    Principal and tool authorization are also imported before sandbox setup.
    Those lightweight helpers must not initialize the sandbox package as a
    side effect.
    """
    if name == "authorize_sandbox_execution":
        from alpha.authz.sandbox_authz import authorize_sandbox_execution

        return authorize_sandbox_execution
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
