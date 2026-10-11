"""Graph-plane failure classification.

This module is the **bridge** between graph-plane failures and
:mod:`alpha.errors.registry`, the single owner of stable error codes. It does
not invent codes; it maps graph-specific failure conditions onto the codes
that already exist, and raises :class:`CodedError` with the right code so the
SSE payload, log record, metric label, and recovery hint all derive from one
definition.

Why a bridge instead of new codes
---------------------------------
The registry's docstring is explicit: "Codes are part of the public payload
surface, so they are append-only: never repurpose or renumber an existing
code, and never widen a code's severity in place. Add a new one." Adding a
code is a contract change that affects every consumer of the error surface.
Most graph failures already have a home:

* ``DEPENDENCY_CYCLE`` → no existing code; this is a new one.
* ``PLAN_VALIDATION_FAILED`` → no existing code; this is a new one.
* ``CAPABILITY_UNAVAILABLE`` → ``DEPENDENCY_UNAVAILABLE`` (a required
  capability is a dependency that is not available).
* ``MODEL_TIMEOUT`` → ``TIMEOUT``.
* ``TOOL_PERMISSION_DENIED`` → ``SANDBOX_POLICY_DENIED`` or ``AUTH_FORBIDDEN``.
* ``VERIFICATION_FAILED`` → no existing code; this is a new one.
* ``RETRY_BUDGET_EXHAUSTED`` → ``RUN_QUOTA_EXCEEDED``.
* ``USER_APPROVAL_REQUIRED`` → no existing code; this is a new one.
* ``USER_REJECTED`` → no existing code; this is a new one.
* ``CANCELLED`` → ``CANCELLED``.
* ``RESOURCE_LIMIT`` → ``RUN_QUOTA_EXCEEDED``.
* ``INTERNAL_ERROR`` → ``INTERNAL_ERROR``.

The new codes are added to the registry in the same change set, following the
append-only rule.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from alpha.errors.registry import CodedError, ErrorDefinition

__all__ = [
    "GraphFailureClass",
    "classify_graph_failure",
    "raise_graph_failure",
]


class GraphFailureClass(StrEnum):
    """Graph-plane failure classes.

    These are **not** error codes. They are the graph plane's own taxonomy,
    used for metrics, dashboards, and retry decisions. Each one maps to
    exactly one registry code via :func:`classify_graph_failure`.
    """

    INTENT_MISMATCH = "intent_mismatch"
    PLAN_VALIDATION_FAILED = "plan_validation_failed"
    DEPENDENCY_CYCLE = "dependency_cycle"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_RATE_LIMIT = "model_rate_limit"
    MODEL_INVALID_OUTPUT = "model_invalid_output"
    TOOL_ARGUMENT_INVALID = "tool_argument_invalid"
    TOOL_PERMISSION_DENIED = "tool_permission_denied"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_NONZERO_EXIT = "tool_nonzero_exit"
    ARTIFACT_WRITE_FAILED = "artifact_write_failed"
    CHECKPOINT_FAILED = "checkpoint_failed"
    STORE_UNAVAILABLE = "store_unavailable"
    WORKER_CRASHED = "worker_crashed"
    WORKER_OUTPUT_INVALID = "worker_output_invalid"
    VERIFICATION_FAILED = "verification_failed"
    RETRY_BUDGET_EXHAUSTED = "retry_budget_exhausted"
    USER_APPROVAL_REQUIRED = "user_approval_required"
    USER_REJECTED = "user_rejected"
    CANCELLED = "cancelled"
    RESOURCE_LIMIT = "resource_limit"
    INTERNAL_ERROR = "internal_error"


# The mapping from graph failure class to registry code. This is the **only**
# place the mapping lives, so a code can never mean two things in two places.
_FAILURE_TO_CODE: dict[GraphFailureClass, str] = {
    GraphFailureClass.INTENT_MISMATCH: "INTERNAL_ERROR",
    GraphFailureClass.PLAN_VALIDATION_FAILED: "PLAN_VALIDATION_FAILED",
    GraphFailureClass.DEPENDENCY_CYCLE: "DEPENDENCY_CYCLE",
    GraphFailureClass.CAPABILITY_UNAVAILABLE: "DEPENDENCY_UNAVAILABLE",
    GraphFailureClass.MODEL_TIMEOUT: "TIMEOUT",
    GraphFailureClass.MODEL_RATE_LIMIT: "MODEL_PROVIDER_RATE_LIMITED",
    GraphFailureClass.MODEL_INVALID_OUTPUT: "MODEL_RESPONSE_INVALID",
    GraphFailureClass.TOOL_ARGUMENT_INVALID: "TOOL_SCHEMA_INVALID",
    GraphFailureClass.TOOL_PERMISSION_DENIED: "SANDBOX_POLICY_DENIED",
    GraphFailureClass.TOOL_TIMEOUT: "TIMEOUT",
    GraphFailureClass.TOOL_NONZERO_EXIT: "TOOL_EXECUTION_FAILED",
    GraphFailureClass.ARTIFACT_WRITE_FAILED: "STORAGE_WRITE_FAILED",
    GraphFailureClass.CHECKPOINT_FAILED: "CHECKPOINT_WRITE_FAILED",
    GraphFailureClass.STORE_UNAVAILABLE: "PERSISTENCE_UNAVAILABLE",
    GraphFailureClass.WORKER_CRASHED: "RUN_EXECUTION_FAILED",
    GraphFailureClass.WORKER_OUTPUT_INVALID: "MODEL_RESPONSE_INVALID",
    GraphFailureClass.VERIFICATION_FAILED: "VERIFICATION_FAILED",
    GraphFailureClass.RETRY_BUDGET_EXHAUSTED: "RUN_QUOTA_EXCEEDED",
    GraphFailureClass.USER_APPROVAL_REQUIRED: "USER_APPROVAL_REQUIRED",
    GraphFailureClass.USER_REJECTED: "USER_REJECTED",
    GraphFailureClass.CANCELLED: "CANCELLED",
    GraphFailureClass.RESOURCE_LIMIT: "RUN_QUOTA_EXCEEDED",
    GraphFailureClass.INTERNAL_ERROR: "INTERNAL_ERROR",
}


def classify_graph_failure(failure_class: GraphFailureClass) -> ErrorDefinition:
    """Return the registry definition for a graph failure class.

    Raises ``KeyError`` if the class is not in the mapping. That is a bug in
    this module, not a runtime condition, so it is allowed to propagate.
    """
    from alpha.errors.registry import require_definition

    code = _FAILURE_TO_CODE[failure_class]
    return require_definition(code)


def raise_graph_failure(
    failure_class: GraphFailureClass,
    detail: str = "",
    *,
    context: dict[str, Any] | None = None,
) -> None:
    """Raise a :class:`CodedError` with the right registry code.

    This is the **only** way graph-plane code should raise a coded failure.
    Raising ``CodedError`` directly with a code string would bypass the mapping
    and let a code drift from its definition.
    """
    definition = classify_graph_failure(failure_class)
    raise CodedError(definition.code, detail, context=context)
