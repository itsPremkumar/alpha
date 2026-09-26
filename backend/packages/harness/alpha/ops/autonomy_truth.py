"""Offline truth and recovery decisions for autonomous operation.

This module turns existing runtime evidence into bounded decisions. It does not
execute tools, call providers, or persist user data. The companion
``autonomy_control`` tool exposes the same pure functions to agents.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

SCHEMA_VERSION = "alpha.autonomy-truth.v1"
MAX_INPUT_CHARS = 256_000
MAX_EVENTS = 2_000
MAX_SUMMARY_CHARS = 4_000
MAX_ERROR_SUMMARY_CHARS = 500

CapabilityStatus = Literal["available", "degraded", "unavailable", "unknown"]
FailureCategory = Literal["permission", "sandbox", "dependency", "provider", "network", "resource", "input", "unknown"]

KNOWN_NON_CAPABILITIES: dict[str, str] = {
    "unbounded_autonomy": "No system may bypass authorization, safety, budget, or human-approval boundaries.",
    "credential_inference": "Credentials cannot be inferred, reconstructed, or exposed from model output.",
    "universal_os_control": "Computer control requires a configured, authorized adapter; it is not implicit.",
    "guaranteed_success": "No autonomous operation can guarantee success without independent evidence.",
}

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)bearer\s+[^\s,;]+"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*(?:basic\s+)?)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret)"
            r"([\"']?\s*[:=]\s*[\"']?)[^\s,;\"']+"
        ),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{6,}\b"), "[REDACTED]"),
    (
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "[REDACTED_JWT]",
    ),
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
)


@dataclass(frozen=True)
class CapabilityDecision:
    capability: str
    status: CapabilityStatus
    allowed: bool
    reason: str
    next_action: str
    evidence: tuple[str, ...] = ()
    requires_approval: bool = False
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = list(self.evidence)
        return data


@dataclass(frozen=True)
class FailureAdvice:
    category: FailureCategory
    retryable: bool
    user_message: str
    next_steps: tuple[str, ...]
    safe_summary: str
    redacted: bool
    retry_budget: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["next_steps"] = list(self.next_steps)
        return data


@dataclass(frozen=True)
class ActivityDigest:
    step_count: int
    success_count: int
    failure_count: int
    retry_count: int
    approval_count: int
    tool_counts: dict[str, int]
    edited_files: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_seconds: float | None
    summary: str
    recommendations: tuple[str, ...]

    @property
    def edited_file_count(self) -> int:
        return len(self.edited_files)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tool_counts"] = dict(sorted(self.tool_counts.items()))
        data["edited_files"] = list(self.edited_files)
        data["recommendations"] = list(self.recommendations)
        return data


def _text(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(context or {})


def _int_context(context: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = context.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def assess_capability(capability: str, context: Mapping[str, Any] | None = None) -> CapabilityDecision:
    """Return an honest, evidence-based decision for one requested capability."""

    name = _text(capability)
    ctx = _context(context)
    if not name:
        return CapabilityDecision("unknown", "unknown", False, "capability name is required", "name the capability before planning", confidence=1.0)
    if name in KNOWN_NON_CAPABILITIES:
        return CapabilityDecision(name, "unavailable", False, KNOWN_NON_CAPABILITIES[name], "choose a supported capability or change the objective", evidence=("registered non-capability",), confidence=1.0)

    if name == "project_documentation":
        return CapabilityDecision(name, "available", True, "the offline self-documentation tool is registered", "search current project docs before configuration claims", evidence=("search_project_docs",), confidence=1.0)

    if name == "model_execution":
        if ctx.get("current_model_observed") is True:
            return CapabilityDecision(
                name,
                "available",
                True,
                "the current model successfully invoked an injected runtime tool",
                "continue within the active model, token, and cost budgets",
                evidence=("current_model_tool_invocation",),
                confidence=1.0,
            )
        model_name = ctx.get("model_name")
        configured = ctx.get("model_available")
        if isinstance(model_name, str) and model_name and configured is True:
            return CapabilityDecision(
                name,
                "available",
                True,
                f"model '{model_name}' is present in the server configuration",
                "run a minimal provider health check before a long task",
                evidence=(f"configured_model={model_name}",),
                confidence=0.8,
            )
        if configured is False:
            return CapabilityDecision(
                name,
                "unavailable",
                False,
                "the selected model is not present in the server configuration",
                "select a configured model or restore its provider configuration",
                confidence=1.0,
            )
        return CapabilityDecision(
            name,
            "unknown",
            False,
            "no current-model observation is available",
            "invoke this tool through the active agent runtime",
            confidence=0.2,
        )

    if name == "tool_execution":
        if ctx.get("tool_execution_observed") is True:
            return CapabilityDecision(
                name,
                "available",
                True,
                "the current runtime successfully dispatched an injected tool",
                "continue within the tools visible to the current policy scope",
                evidence=("current_tool_invocation",),
                confidence=1.0,
            )
        count = _int_context(ctx, "tool_count", "available_tool_count")
        if count is None:
            return CapabilityDecision(name, "unknown", False, "tool assembly has not been observed", "provide the resolved tool count or run a tool-availability probe", confidence=0.2)
        if count <= 0:
            return CapabilityDecision(name, "unavailable", False, "no tools are available in the current assembly", "enable an approved tool group or change the task", evidence=(f"tool_count={count}",), confidence=1.0)
        return CapabilityDecision(name, "available", True, f"{count} tools are available", "use an available tool within policy", evidence=(f"tool_count={count}",), confidence=1.0)

    if name == "sandbox_execution":
        provider = _text(ctx.get("sandbox_provider"))
        if not provider:
            return CapabilityDecision(name, "unknown", False, "sandbox provider is not present in the supplied context", "inspect the resolved sandbox provider", confidence=0.2)
        if provider in {"none", "disabled", "unavailable"}:
            return CapabilityDecision(name, "unavailable", False, "the configured sandbox provider is disabled", "enable a local sandbox or choose a non-sandbox tool", evidence=(f"sandbox_provider={provider}",), confidence=1.0)
        if provider in {"local", "docker", "container", "a2a", "boxlite"}:
            return CapabilityDecision(name, "available", True, f"sandbox provider {provider} is configured", "run inside the configured sandbox", evidence=(f"sandbox_provider={provider}",), confidence=0.95)
        return CapabilityDecision(
            name,
            "degraded",
            True,
            f"sandbox provider {provider} requires an environment-specific probe",
            "verify provider readiness before execution",
            evidence=(f"sandbox_provider={provider}",),
            requires_approval=provider not in {"local", "docker"},
            confidence=0.65,
        )

    if name == "vision":
        supported = ctx.get("model_supports_vision")
        if supported is True:
            return CapabilityDecision(name, "available", True, "the selected model declares vision support", "use the vision tool and verify image evidence", evidence=("model_supports_vision=true",), confidence=1.0)
        if supported is False:
            return CapabilityDecision(name, "unavailable", False, "the selected model does not declare vision support", "select a vision-capable model or use a text alternative", evidence=("model_supports_vision=false",), confidence=1.0)
        return CapabilityDecision(name, "unknown", False, "model vision capability is unknown", "inspect the selected model capabilities", confidence=0.2)

    if name == "network":
        policy = _text(ctx.get("network_policy"))
        if not policy:
            return CapabilityDecision(name, "unknown", False, "network policy is not present in the supplied context", "inspect the effective sandbox network policy", confidence=0.2)
        if policy in {"isolated", "none", "deny", "disabled"}:
            return CapabilityDecision(name, "unavailable", False, "network access is disabled by policy", "use local resources or request a scoped policy change", evidence=(f"network_policy={policy}",), confidence=1.0)
        if policy in {"allowlist", "open"}:
            approval = policy == "open"
            return CapabilityDecision(
                name,
                "degraded" if approval else "available",
                True,
                f"network policy is {policy}",
                "use allowlisted destinations" if not approval else "confirm external destinations before use",
                evidence=(f"network_policy={policy}",),
                requires_approval=approval,
                confidence=0.95,
            )
        return CapabilityDecision(name, "degraded", True, f"unrecognized network policy: {policy}", "verify the effective policy before network access", evidence=(f"network_policy={policy}",), confidence=0.5)

    if name == "mcp_connectors":
        servers = ctx.get("enabled_mcp_servers")
        if isinstance(servers, (list, tuple, set)):
            count = len(servers)
            if count == 0:
                return CapabilityDecision(name, "unavailable", False, "no MCP connectors are enabled", "enable a trusted connector or use built-in tools", evidence=("enabled_mcp_servers=0",), confidence=1.0)
            return CapabilityDecision(name, "available", True, f"{count} MCP connector(s) are enabled", "inspect connector tools before calling them", evidence=(f"enabled_mcp_servers={count}",), confidence=1.0)
        return CapabilityDecision(name, "unknown", False, "MCP connector state was not supplied", "inspect the enabled connector registry", confidence=0.2)

    if name in {"public_skills", "skills"}:
        count = _int_context(ctx, "public_skill_count", "skill_count")
        if count is None:
            return CapabilityDecision(name, "unknown", False, "skill inventory was not supplied", "inspect the enabled skill registry", confidence=0.2)
        if count <= 0:
            return CapabilityDecision(name, "unavailable", False, "no public skills are available", "install or enable a reviewed skill", evidence=(f"skill_count={count}",), confidence=1.0)
        return CapabilityDecision(name, "available", True, f"{count} skill(s) are available", "select a skill whose trigger matches the task", evidence=(f"skill_count={count}",), confidence=1.0)

    if name == "local_inference":
        models = ctx.get("models")
        available = ctx.get("model_available")
        if available is False or models == []:
            return CapabilityDecision(name, "unavailable", False, "no local model is available in the supplied context", "configure a local model or use an explicitly configured provider", confidence=1.0)
        if available is True or isinstance(models, (list, tuple)) and models:
            return CapabilityDecision(name, "available", True, "a local model is available", "select the smallest capable local model", evidence=("local_model_available",), confidence=0.95)
        return CapabilityDecision(name, "unknown", False, "local model availability is unknown", "inspect configured model providers", confidence=0.2)

    if name == "external_side_effect":
        if ctx.get("server_approval_verified") is True:
            return CapabilityDecision(
                name,
                "available",
                True,
                "the execution layer supplied verified server-owned approval",
                "execute once and collect a receipt",
                evidence=("server_approval_verified",),
                requires_approval=True,
                confidence=1.0,
            )
        return CapabilityDecision(
            name,
            "degraded",
            False,
            "external side effects require a server-resolved human decision",
            "prepare an operation dossier and request project approval",
            evidence=("no_server_approval",),
            requires_approval=True,
            confidence=1.0,
        )

    if name == "filesystem_mutation":
        if ctx.get("server_write_context_verified") is True:
            return CapabilityDecision(
                name,
                "available",
                True,
                "the execution layer supplied a verified writable context",
                "use checkpoints and verify the resulting diff",
                evidence=("server_write_context_verified",),
                requires_approval=True,
                confidence=1.0,
            )
        return CapabilityDecision(
            name,
            "degraded",
            False,
            "a model-supplied context cannot authorize workspace mutation",
            "use the existing execution-mode and approval middleware",
            evidence=("no_server_write_context",),
            requires_approval=True,
            confidence=1.0,
        )

    return CapabilityDecision(name, "unavailable", False, f"'{name}' is not a registered capability", "choose a capability from the supported registry", confidence=1.0)


_REQUIRED_READINESS_CAPABILITIES = (
    "model_execution",
    "tool_execution",
    "project_documentation",
)
_READINESS_CAPABILITIES = (
    *_REQUIRED_READINESS_CAPABILITIES,
    "sandbox_execution",
    "vision",
    "network",
    "mcp_connectors",
    "public_skills",
)


def assess_autonomy(context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a fail-closed readiness decision for basic autonomous operation.

    Only the current model/tool loop and offline documentation are required.
    Sandboxing, vision, network, MCP connectors, and public skills are reported
    as optional capabilities and limitations; their absence does not make a
    valid offline agent loop unready.
    """

    ctx = _context(context)
    decisions = [assess_capability(name, ctx) for name in _READINESS_CAPABILITIES]
    required = {item.capability: item for item in decisions if item.capability in _REQUIRED_READINESS_CAPABILITIES}
    required_unavailable = [item for item in required.values() if item.status == "unavailable"]
    required_unknown = [item for item in required.values() if item.status == "unknown"]
    required_degraded = [item for item in required.values() if item.status == "degraded"]
    optional_limits = [item for item in decisions if item.capability not in _REQUIRED_READINESS_CAPABILITIES and item.status != "available"]
    if required_unavailable:
        status = "blocked"
    elif required_unknown or required_degraded:
        status = "degraded"
    else:
        status = "ready"

    next_actions: list[str] = []
    for item in (*required_unavailable, *required_unknown, *required_degraded, *optional_limits):
        if item.next_action not in next_actions:
            next_actions.append(item.next_action)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "ready": status == "ready",
        "required_capabilities": list(_REQUIRED_READINESS_CAPABILITIES),
        "capabilities": [item.to_dict() for item in decisions],
        "blocking_capabilities": [item.capability for item in required_unavailable],
        "unknown_capabilities": [item.capability for item in required_unknown],
        "degraded_capabilities": [item.capability for item in required_degraded],
        "optional_limitations": [item.capability for item in optional_limits],
        "next_actions": next_actions[:8],
        "offline": True,
    }


def _redact_error(value: str) -> tuple[str, bool]:
    redacted = value
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    bounded = redacted[: MAX_ERROR_SUMMARY_CHARS - 1]
    if len(redacted) > MAX_ERROR_SUMMARY_CHARS - 1:
        bounded = bounded.rstrip() + "…"
    return bounded, redacted != value


def redact_text(value: str, *, max_chars: int = MAX_ERROR_SUMMARY_CHARS) -> tuple[str, bool]:
    """Redact common credential forms and bound arbitrary model-visible text."""
    raw = str(value or "")[:MAX_INPUT_CHARS]
    summary, redacted = _redact_error(raw)
    try:
        limit = max(32, min(MAX_ERROR_SUMMARY_CHARS, int(max_chars)))
    except (TypeError, ValueError):
        limit = MAX_ERROR_SUMMARY_CHARS
    if len(summary) > limit:
        summary = summary[: max(1, limit - 1)].rstrip() + "…"
    return summary, redacted


def classify_failure(error: str) -> FailureAdvice:
    """Classify an error and return a safe, bounded recovery plan."""

    raw = str(error or "")[:MAX_INPUT_CHARS]
    text = raw.casefold()
    if any(token in text for token in ("permission denied", "permissionerror", "forbidden", "approval required", "access denied")):
        category: FailureCategory = "permission"
        retryable = False
        user_message = "The action needs permission or approval before it can continue."
        steps = ("choose a read-only alternative if available", "prepare an approval request with the exact target and impact", "retry only after the decision is recorded")
    elif any(token in text for token in ("sandbox", "container runtime", "docker daemon", "no such container")):
        category = "sandbox"
        retryable = True
        user_message = "The isolated execution environment is unavailable."
        steps = ("check sandbox provider readiness", "retry once with the same bounded action", "fall back to a safe local alternative or escalate")
    elif any(token in text for token in ("modulenotfounderror", "importerror", "missing dependency", "no module named", "package not found")):
        category = "dependency"
        retryable = False
        user_message = "A required local dependency is missing."
        steps = ("identify the missing package from the redacted error", "install or enable the reviewed dependency", "run the focused verification before continuing")
    elif any(token in text for token in ("401", "403", "api key", "invalid token", "unauthorized", "forbidden provider", "429", "rate limit", "quota")):
        category = "provider"
        retryable = any(token in text for token in ("429", "rate limit", "quota"))
        user_message = "The configured model or integration provider rejected the request."
        steps = ("check provider credentials and endpoint configuration", "select an approved fallback model if available", "retry rate-limit failures with bounded backoff")
    elif any(token in text for token in ("timeout", "timed out", "dns", "connection", "network", "socket", "temporarily unavailable")):
        category = "network"
        retryable = True
        user_message = "A transient network or remote-service failure interrupted the operation."
        steps = ("verify the endpoint and local connectivity", "retry with bounded exponential backoff", "continue offline or escalate if the endpoint remains unavailable")
    elif any(token in text for token in ("memory", "disk full", "no space", "cpu", "resource exhausted", "out of memory")):
        category = "resource"
        retryable = False
        user_message = "The host does not currently have enough safe execution capacity."
        steps = ("reduce worker and context concurrency", "inspect the resource advice and reclaim safe caches", "retry after headroom is restored")
    elif any(token in text for token in ("invalid input", "invalid user input", "user input", "validation", "schema", "bad request", "missing required")):
        category = "input"
        retryable = False
        user_message = "The request or tool arguments need correction before execution."
        steps = ("validate the request against the tool schema", "correct the reported field without inventing values", "run the smallest verification first")
    else:
        category = "unknown"
        retryable = False
        user_message = "The failure is not safely classifiable from the available evidence."
        steps = ("capture a bounded, redacted error summary", "inspect recent trajectory events and local diagnostics", "escalate instead of blindly retrying")
    summary, redacted = _redact_error(raw)
    if not summary:
        summary = "No error details were provided."
    return FailureAdvice(category, retryable, user_message, tuple(steps), summary, redacted, 2 if retryable else 0)


def _event_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        if any(key in value for key in ("type", "event_type", "tool", "tool_name", "status", "outcome")):
            return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        candidate = to_dict()
        if isinstance(candidate, Mapping):
            return candidate
    return None


def _event_status(event: Mapping[str, Any]) -> str:
    value = _text(event.get("status") or event.get("outcome"))
    if value in {"success", "succeeded", "ok", "completed", "complete"}:
        return "success"
    if value in {"failure", "failed", "error", "denied", "blocked"}:
        return "failure"
    if value in {"retry", "retrying", "recover", "recovered"}:
        return "retry"
    return "other"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if parsed == parsed and abs(parsed) != float("inf") else None
    return None


def _timestamp(value: Any) -> float | None:
    numeric = _number(value)
    if numeric is not None:
        return numeric
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _input_file(event: Mapping[str, Any]) -> str | None:
    tool_input = event.get("tool_input") or event.get("input") or event.get("arguments")
    if not isinstance(tool_input, Mapping):
        return None
    for key in ("path", "file_path", "filename", "target"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value[:300]
    return None


def _tool_name(event: Mapping[str, Any]) -> str:
    value = event.get("tool") or event.get("tool_name") or event.get("name")
    return str(value or "unknown")[:120]


def build_activity_digest(events: Iterable[Any] | None, *, max_events: int = MAX_EVENTS, max_chars: int = MAX_SUMMARY_CHARS) -> ActivityDigest:
    """Summarize existing trajectory/ledger events without echoing raw payloads."""

    try:
        limit = max(1, min(MAX_EVENTS, int(max_events)))
    except (TypeError, ValueError):
        limit = MAX_EVENTS
    try:
        char_limit = max(120, min(MAX_SUMMARY_CHARS, int(max_chars)))
    except (TypeError, ValueError):
        char_limit = MAX_SUMMARY_CHARS

    step_count = success_count = failure_count = retry_count = approval_count = 0
    input_tokens = output_tokens = 0
    cost_usd = 0.0
    tool_counts: Counter[str] = Counter()
    edited_files: set[str] = set()
    timestamps: list[float] = []

    for raw_event in events or ():
        if step_count >= limit:
            break
        event = _event_mapping(raw_event)
        if event is None:
            continue
        step_count += 1
        event_type = _text(event.get("type") or event.get("event_type"))
        status = _event_status(event)
        if status == "success":
            success_count += 1
        elif status == "failure":
            failure_count += 1
        elif status == "retry":
            retry_count += 1
        if "approval" in event_type or event.get("requires_approval") is True:
            approval_count += 1
        tool = _tool_name(event)
        if tool != "unknown":
            tool_counts[tool] += 1
        tool_lower = tool.casefold()
        if any(marker in tool_lower for marker in ("write", "edit", "str_replace", "patch", "format")):
            file_name = _input_file(event)
            if file_name:
                edited_files.add(hashlib.sha256(file_name.encode("utf-8")).hexdigest()[:12])
        usage = event.get("usage") if isinstance(event.get("usage"), Mapping) else {}
        input_value = _number(event.get("input_tokens") or event.get("prompt_tokens") or usage.get("input_tokens"))
        output_value = _number(event.get("output_tokens") or event.get("completion_tokens") or usage.get("output_tokens"))
        if input_value is not None and input_value >= 0:
            input_tokens += int(input_value)
        if output_value is not None and output_value >= 0:
            output_tokens += int(output_value)
        cost_value = _number(event.get("cost_usd") or event.get("cost") or usage.get("cost_usd"))
        if cost_value is not None and cost_value >= 0:
            cost_usd += cost_value
        for key in ("created_at", "timestamp", "started_at", "completed_at"):
            parsed = _timestamp(event.get(key))
            if parsed is not None:
                timestamps.append(parsed)
                break

    duration = max(timestamps) - min(timestamps) if len(timestamps) >= 2 else None
    recommendations: list[str] = []
    if failure_count:
        recommendations.append("classify the failures before choosing a retry or fallback")
    if approval_count:
        recommendations.append("prepare a concise approval briefing for pending external effects")
    if not recommendations:
        recommendations.append("continue with the next evidence-backed step")
    cost_text = f"${cost_usd:.4f}" if cost_usd else "$0"
    summary = (
        f"{step_count} recorded steps: {success_count} succeeded, {failure_count} failed, "
        f"{retry_count} retried, and {approval_count} approval request(s); "
        f"{len(tool_counts)} tool type(s), {len(edited_files)} edited file(s), "
        f"{input_tokens} input and {output_tokens} output tokens, estimated spend {cost_text}."
    )
    if len(summary) > char_limit:
        summary = summary[: max(1, char_limit - 1)].rstrip() + "…"
    return ActivityDigest(
        step_count=step_count,
        success_count=success_count,
        failure_count=failure_count,
        retry_count=retry_count,
        approval_count=approval_count,
        tool_counts=dict(tool_counts),
        edited_files=tuple(sorted(edited_files)[:50]),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(cost_usd, 6),
        duration_seconds=round(duration, 3) if duration is not None else None,
        summary=summary,
        recommendations=tuple(recommendations),
    )


__all__ = [
    "ActivityDigest",
    "CapabilityDecision",
    "FailureAdvice",
    "KNOWN_NON_CAPABILITIES",
    "SCHEMA_VERSION",
    "assess_autonomy",
    "assess_capability",
    "build_activity_digest",
    "classify_failure",
    "redact_text",
]
