"""Built-in autonomy truth, failure, and activity inspection tool."""

# NOTE: no ``from __future__ import annotations`` — LangChain's injected-argument
# detection requires the concrete ``Runtime`` annotation object.

import json
from typing import Any

from langchain.tools import tool

from alpha.ops.autonomy_truth import (
    KNOWN_NON_CAPABILITIES,
    MAX_INPUT_CHARS,
    assess_autonomy,
    assess_capability,
    build_activity_digest,
    classify_failure,
)
from alpha.ops.recovery_brief import build_recovery_brief
from alpha.ops.runtime_readiness import collect_server_readiness
from alpha.tools.types import Runtime


def _json_input(raw: str, *, expected: type, default: Any) -> tuple[Any | None, dict[str, Any] | None]:
    if len(raw) > MAX_INPUT_CHARS:
        return None, {"error": "input_too_large", "max_chars": MAX_INPUT_CHARS}
    if not raw.strip():
        return default, None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None, {"error": "invalid_json"}
    if not isinstance(value, expected):
        return None, {"error": "invalid_json", "expected": expected.__name__}
    return value, None


@tool("autonomy_control", parse_docstring=True)
def autonomy_control(
    runtime: Runtime,
    action: str = "readiness",
    capability: str = "",
    error: str = "",
    events_json: str = "[]",
    max_events: int = 2000,
    max_chars: int = 4000,
) -> dict[str, Any]:
    """Inspect server-observed autonomy readiness, failures, or activity.

    This free, offline decision layer never calls a model, provider, network, or
    external service. ``readiness`` ignores model-authored capability claims and
    derives facts from the injected runtime plus server configuration. ``recovery``
    returns a compact, redacted current-thread resume brief; ``failure`` chooses
    a bounded retry/fallback/escalation path, and ``activity`` turns existing
    trajectory or ledger events into a privacy-preserving digest.

    Args:
        action: One of ``readiness``, ``capability``, ``failure``, ``recovery``, ``activity``, or ``known_limits``.
        capability: Capability name for action ``capability``.
        error: Failure text for action ``failure``; output is redacted and bounded.
        events_json: JSON array of existing event mappings for action ``activity``.
        max_events: Maximum events to inspect (1-2000).
        max_chars: Maximum digest summary length.
    """

    normalized = (action or "readiness").strip().lower()
    try:
        if normalized in {"readiness", "capability"}:
            context, diagnostics = collect_server_readiness(runtime)
            if normalized == "readiness":
                return {
                    "success": True,
                    "action": normalized,
                    "free_and_offline": True,
                    "evidence_source": "server_runtime",
                    "probe_diagnostics": diagnostics,
                    **assess_autonomy(context),
                }
            if not capability.strip():
                return {
                    "success": False,
                    "action": normalized,
                    "error": "capability_required",
                    "free_and_offline": True,
                }
            decision = assess_capability(capability, context).to_dict()
            return {
                "success": True,
                "action": normalized,
                "free_and_offline": True,
                "evidence_source": "server_runtime",
                "probe_diagnostics": diagnostics,
                "decision": decision,
            }
        if normalized == "recovery":
            state = getattr(runtime, "state", None)
            brief = build_recovery_brief(state if isinstance(state, dict) else None, max_chars=max_chars)
            return {
                "success": True,
                "action": normalized,
                "free_and_offline": True,
                "recovery": brief,
            }
        if normalized == "failure":
            advice = classify_failure(error)
            return {"success": True, "action": normalized, "free_and_offline": True, **advice.to_dict()}
        if normalized == "activity":
            events, parse_error = _json_input(events_json, expected=list, default=[])
            if parse_error:
                return {"success": False, "action": normalized, **parse_error, "free_and_offline": True}
            digest = build_activity_digest(events, max_events=max_events, max_chars=max_chars).to_dict()
            return {"success": True, "action": normalized, "free_and_offline": True, "digest": digest}
        if normalized == "known_limits":
            return {
                "success": True,
                "action": normalized,
                "free_and_offline": True,
                "non_capabilities": dict(KNOWN_NON_CAPABILITIES),
            }
        return {
            "success": False,
            "action": normalized,
            "error": "unsupported_action",
            "supported_actions": ["readiness", "capability", "failure", "recovery", "activity", "known_limits"],
            "free_and_offline": True,
        }
    except Exception as exc:  # pragma: no cover - defensive model-tool boundary
        return {
            "success": False,
            "action": normalized,
            "error": "autonomy_control_unavailable",
            "detail": type(exc).__name__,
            "free_and_offline": True,
        }


__all__ = ["autonomy_control"]
