"""Registry-derivation helpers: UNMEASURED passports built from what exists.

Phase A of the capability mesh derives a passport for every runnable
configuration the harness already knows about — tools, model configs, skill
bundles, agents — so later phases have a record to *fill with measurements*
rather than invent one when the first measurement arrives.

Two rules hold this module together:

* **A derived passport is always UNMEASURED with ``sample_count=0``.** The
  mesh never claims a measurement it does not have; Phase B wires real
  outcome data through :func:`alpha.capability_mesh.models.derive_evidence_level`.
* **Registry knowledge is injected, never imported.** The harness tool
  registry is reachable only through the tool graph, whose import is
  heavyweight and side-effecting; instead of coupling this package to it,
  callers pass ``known_tool_names`` and the helper refuses an unknown tool
  *when the set is provided*. Without the set the helper is a pure builder —
  it adds no knowledge and therefore cannot invent any.
"""

from __future__ import annotations

from collections.abc import Sequence

from alpha.capability_mesh.models import CapabilityEstimate, CapabilityPassport, EntityKind, EvidenceLevel

#: The declared wildcard task category: an estimate that answers any task
#: when no narrower category has been measured. Never a substitute for one.
WILDCARD_TASK_CATEGORY = "*"


def _require_text(value: str, field: str) -> str:
    if not value:
        raise ValueError(f"{field} must be non-empty")
    return value


def _unmeasured(capability: str) -> CapabilityEstimate:
    """One honest UNMEASURED estimate — the only kind Phase A may mint."""
    return CapabilityEstimate(capability=capability, task_category=WILDCARD_TASK_CATEGORY, evidence_level=EvidenceLevel.UNMEASURED)


def tool_passport(
    tool_name: str,
    *,
    version: str,
    required_tools: Sequence[str] = (),
    known_tool_names: frozenset[str] | None = None,
) -> CapabilityPassport:
    """An UNMEASURED passport for one registered tool.

    ``known_tool_names`` is the injected registry: when provided, a tool
    outside it is refused by name rather than minting a passport for
    something that does not exist.
    """
    _require_text(tool_name, "tool_name")
    _require_text(version, "version")
    if known_tool_names is not None and tool_name not in known_tool_names:
        raise ValueError(f"unknown tool {tool_name!r}: not present in the provided registry names")
    return CapabilityPassport(
        entity_id=f"tool:{tool_name}",
        entity_type=EntityKind.TOOL,
        version=version,
        required_tools=tuple(required_tools),
        capabilities=(_unmeasured(f"use:{tool_name}"),),
    )


def model_passport(
    provider: str,
    model_id: str,
    *,
    version: str,
    capabilities: Sequence[str] = (),
) -> CapabilityPassport:
    """An UNMEASURED passport for one model config, one estimate per capability.

    ``version`` is the config/revision identity: a model or config update
    produces a new version, which is what lets stale measurements be
    invalidated later instead of silently outliving the thing they measured.
    """
    _require_text(provider, "provider")
    _require_text(model_id, "model_id")
    _require_text(version, "version")
    unique = list(dict.fromkeys(capabilities))
    return CapabilityPassport(
        entity_id=f"model:{provider}/{model_id}",
        entity_type=EntityKind.MODEL_CONFIG,
        version=version,
        model_provider=provider,
        model_id=model_id,
        capabilities=tuple(_unmeasured(name) for name in unique),
    )


def skill_passport(
    skill_name: str,
    *,
    version: str,
    required_tools: Sequence[str] = (),
) -> CapabilityPassport:
    """An UNMEASURED passport for one skill bundle."""
    _require_text(skill_name, "skill_name")
    _require_text(version, "version")
    return CapabilityPassport(
        entity_id=f"skill:{skill_name}",
        entity_type=EntityKind.SKILL_BUNDLE,
        version=version,
        required_tools=tuple(required_tools),
        capabilities=(_unmeasured(f"use:{skill_name}"),),
    )


def agent_passport(
    agent_id: str,
    *,
    version: str,
    capabilities: Sequence[str] = (),
    required_tools: Sequence[str] = (),
) -> CapabilityPassport:
    """An UNMEASURED passport for one agent configuration.

    An agent with no declared capabilities is honest — the passport exists
    and says "unmeasured", which is the correct answer for a fresh
    configuration, not a defect to paper over.
    """
    _require_text(agent_id, "agent_id")
    _require_text(version, "version")
    unique = list(dict.fromkeys(capabilities))
    return CapabilityPassport(
        entity_id=f"agent:{agent_id}",
        entity_type=EntityKind.AGENT,
        version=version,
        required_tools=tuple(required_tools),
        capabilities=tuple(_unmeasured(name) for name in unique),
    )


__all__ = [
    "WILDCARD_TASK_CATEGORY",
    "agent_passport",
    "model_passport",
    "skill_passport",
    "tool_passport",
]
