"""Effective, policy-filtered catalog snapshot for one discovery session.

Source of truth
---------------
Alpha already has exactly one production tool assembly path
(:func:`alpha.tools.tools.get_available_tools`) and one builtin registry
(:data:`alpha.tools.tools.BUILTIN_TOOLS`), plus a read-only
``workflow/registry`` ``tools`` kind over the same list. This module does
**not** create a second source of truth: it consumes the already-assembled
``BaseTool`` objects a caller passes in, and it reuses the repo's MCP source
tag helpers (:mod:`alpha.tools.mcp_metadata`) instead of sniffing metadata
itself.

Policy filter
-------------
The only authority in the tool layer is the skill ``allowed-tools`` policy
(:func:`alpha.skills.tool_policy.allowed_tool_names_for_skills`, applied by
``SkillToolPolicyMiddleware``). This module therefore accepts the *already
resolved* allowed-name set and intersects against it, rather than inventing a
second permission model. ``allowed_names=None`` means "no restriction", which
is exactly what the upstream helper returns for legacy behavior.

Trust boundary
--------------
``builtin`` and ``plugin`` tools are first-party: their parameter schemas are
authored in this repository and are safe to index, render as an input
signature, and validate before execution. ``mcp`` and ``client`` tools are
untrusted: their parameter schemas are NEVER indexed, never rendered into the
prompt prefix, and are reported as ``input: "unknown"`` until their own
execution boundary provides them. Their *names and descriptions* are also
kept out of the system prompt (see :mod:`alpha.tools.discovery.directory`),
because an external server must not get to write prompt text.

Declared per-tool metadata (optional, read by this module)
---------------------------------------------------------
============================================  ==========================
``BaseTool.metadata`` key                     meaning
============================================  ==========================
``discovery_catalog_mode``                    ``direct-only`` |
                                              ``catalog-eligible``
``discovery_risk_level``                      ``low`` | ``medium`` |
                                              ``high`` | ``critical``
``discovery_permissions``                     list of capability names
``discovery_execution_mode``                  ``parallel`` | ``sequential``
``discovery_input_schema``                    trusted JSON input schema
``discovery_output_schema``                   trusted JSON output schema
``discovery_client_provided``                 marks a client-supplied tool
``discovery_plugin``                          plugin name for the source
============================================  ==========================

Alpha has no per-tool risk/permission registry today, so these default
honestly rather than being invented here: risk ``low``, no required
permissions, ``catalog-eligible``, ``parallel``. Declaring them is how a tool
opts into stricter handling, and the defaults are what every unannotated tool
gets.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from langchain.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_function

from alpha.tools.discovery.telemetry import compute_counter_scope
from alpha.tools.mcp_metadata import get_mcp_source, is_mcp_tool

#: Bound on a rendered input signature, in characters. Keeps a pathological
#: schema from turning one search result into a prompt-sized payload.
INPUT_SIGNATURE_CHAR_MAX = 240
DESCRIPTION_CHAR_MAX = 400
OUTPUT_HINT_CHAR_MAX = 200

#: Sentinel used in results when a schema is deliberately withheld.
UNKNOWN_INPUT = "unknown"

#: Metadata key read for the trusted output schema of a first-party tool.
OUTPUT_SCHEMA_METADATA_KEY = "discovery_output_schema"


class ToolSource(StrEnum):
    """Where a tool came from. ``MCP`` and ``CLIENT`` are untrusted."""

    BUILTIN = "builtin"
    MCP = "mcp"
    PLUGIN = "plugin"
    CLIENT = "client"


#: Sources whose parameter schemas are never indexed, rendered, or validated
#: by this layer. Everything else is first-party and therefore trusted.
UNTRUSTED_SOURCES = frozenset({ToolSource.MCP, ToolSource.CLIENT})


class CatalogMode(StrEnum):
    """Whether a tool is compaction-eligible or must stay directly visible."""

    #: Stays model-visible; excluded from the catalog and from the directory.
    DIRECT_ONLY = "direct-only"
    #: Discoverable through search/describe/call.
    CATALOG_ELIGIBLE = "catalog-eligible"


class ExecutionMode(StrEnum):
    """Mutual-exclusion contract for a tool inside a catalog session."""

    PARALLEL = "parallel"
    SEQUENTIAL = "sequential"


DEFAULT_RISK_LEVEL = "low"
VALID_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})


def is_untrusted(source: ToolSource) -> bool:
    """Whether parameter schemas from *source* must stay out of the index."""
    return source in UNTRUSTED_SOURCES


def _metadata(tool: BaseTool) -> dict[str, Any]:
    value = getattr(tool, "metadata", None)
    return value if isinstance(value, dict) else {}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def resolve_source(tool: BaseTool) -> ToolSource:
    """Classify *tool* using the repo's existing MCP tag, then local metadata."""
    meta = _metadata(tool)
    if is_mcp_tool(tool):
        return ToolSource.MCP
    if meta.get("discovery_client_provided") is True:
        return ToolSource.CLIENT
    if isinstance(meta.get("discovery_plugin"), str) and meta["discovery_plugin"]:
        return ToolSource.PLUGIN
    return ToolSource.BUILTIN


def resolve_catalog_mode(tool: BaseTool) -> CatalogMode:
    """``direct-only`` when the tool declares it, else ``catalog-eligible``."""
    declared = _metadata(tool).get("discovery_catalog_mode")
    if isinstance(declared, str) and declared.strip().lower() == CatalogMode.DIRECT_ONLY.value:
        return CatalogMode.DIRECT_ONLY
    return CatalogMode.CATALOG_ELIGIBLE


def resolve_execution_mode(tool: BaseTool) -> ExecutionMode:
    """``sequential`` when the tool declares it, else ``parallel``."""
    declared = _metadata(tool).get("discovery_execution_mode")
    if isinstance(declared, str) and declared.strip().lower() == ExecutionMode.SEQUENTIAL.value:
        return ExecutionMode.SEQUENTIAL
    return ExecutionMode.PARALLEL


def resolve_risk_level(tool: BaseTool) -> str:
    """Declared risk level, or the honest ``low`` default."""
    declared = _metadata(tool).get("discovery_risk_level")
    if isinstance(declared, str) and declared.strip().lower() in VALID_RISK_LEVELS:
        return declared.strip().lower()
    return DEFAULT_RISK_LEVEL


def resolve_permissions(tool: BaseTool) -> tuple[str, ...]:
    """Declared required permissions, or an empty tuple.

    Permissions are *disclosed* metadata, not an enforcement mechanism: the
    enforcement lives in the policy layer that already exists. Surfacing them
    here is what lets the directory and ``describe`` tell the model what a
    call will need before it asks for one.
    """
    declared = _metadata(tool).get("discovery_permissions")
    if not isinstance(declared, (list, tuple)):
        return ()
    return tuple(sorted({str(item).strip() for item in declared if str(item).strip()}))


def _first_party_input_schema(tool: BaseTool) -> dict[str, Any] | None:
    """Trusted input schema for a first-party tool, else ``None``.

    A declared ``discovery_input_schema`` wins so a tool can narrow the surface
    the model sees; otherwise the schema LangChain already derives from the
    tool is used. Both are first-party artifacts.

    The derived form gets ``additionalProperties: false`` added, because that
    schema is generated from the function signature and therefore already
    describes every accepted argument. Forbidding extras turns a latent
    ``TypeError`` deep inside the tool into an actionable, pre-execution error
    that can suggest the correct parameter name. A *declared* schema is left
    exactly as its author wrote it.
    """
    declared = _metadata(tool).get("discovery_input_schema")
    if isinstance(declared, dict) and declared.get("properties") is not None:
        return declared
    try:
        function = convert_to_openai_function(tool)
    except Exception:
        return None
    parameters = function.get("parameters")
    if not isinstance(parameters, dict) or not parameters.get("properties"):
        return None
    return {**parameters, "additionalProperties": False}


def _trusted_output_schema(tool: BaseTool) -> dict[str, Any] | None:
    """Declared trusted output schema for a first-party tool, else ``None``."""
    declared = _metadata(tool).get(OUTPUT_SCHEMA_METADATA_KEY)
    if not isinstance(declared, dict) or not declared:
        return None
    return declared


def render_input_signature(schema: dict[str, Any] | None) -> str:
    """Render a bounded ``{ name: type; name?: type }`` signature from *schema*.

    Deterministic: properties are emitted in schema order with required ones
    marked non-optional, so an unchanged schema always renders identically.
    """
    if not schema:
        return UNKNOWN_INPUT
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return UNKNOWN_INPUT
    required = schema.get("required")
    required_names = set(required) if isinstance(required, list) else set()
    parts: list[str] = []
    for name, definition in properties.items():
        marker = "" if name in required_names else "?"
        rendered_type = _render_type(definition)
        parts.append(f"{name}{marker}: {rendered_type}")
    return _clip("{ " + "; ".join(parts) + " }", INPUT_SIGNATURE_CHAR_MAX)


def _render_type(definition: Any) -> str:
    if not isinstance(definition, dict):
        return "unknown"
    if "enum" in definition and isinstance(definition["enum"], list) and definition["enum"]:
        options = " | ".join(json.dumps(item, ensure_ascii=False) for item in definition["enum"][:6])
        return f'"{options}"' if len(definition["enum"]) <= 3 else f'"{options}" | ...'
    declared = definition.get("type")
    if isinstance(declared, list) and declared:
        return " | ".join(str(item) for item in declared)
    if isinstance(declared, str):
        return declared
    for key in ("anyOf", "oneOf", "allOf"):
        options = definition.get(key)
        if isinstance(options, list) and options:
            return " | ".join(dict.fromkeys(_render_type(option) for option in options))
    return "unknown"


def render_output_hint(schema: dict[str, Any] | None) -> str:
    """Render a compact trusted output hint, or ``""`` when none is trustworthy."""
    if not schema:
        return ""
    properties = schema.get("properties")
    if isinstance(properties, dict) and properties:
        return _clip("{ " + "; ".join(f"{name}: {_render_type(spec)}" for name, spec in properties.items()) + " }", OUTPUT_HINT_CHAR_MAX)
    declared = schema.get("type")
    if isinstance(declared, str):
        return _clip(declared, OUTPUT_HINT_CHAR_MAX)
    return ""


@dataclass(frozen=True)
class CatalogEntry:
    """One discoverable tool. ``tool`` is the real object; excluded from equality."""

    entry_id: str
    name: str
    source: ToolSource
    description: str
    risk_level: str
    required_permissions: tuple[str, ...]
    catalog_mode: CatalogMode
    execution_mode: ExecutionMode
    input_signature: str
    output_hint: str
    input_schema: dict[str, Any] | None
    output_schema: dict[str, Any] | None
    mcp_server: str | None = None
    tool: BaseTool = field(default=None, repr=False, compare=False)

    @property
    def trusted(self) -> bool:
        """Whether parameter schemas from this entry may be indexed and shown."""
        return not is_untrusted(self.source)

    @property
    def sequential_only(self) -> bool:
        return self.execution_mode is ExecutionMode.SEQUENTIAL

    def public_payload(self) -> dict[str, Any]:
        """Cache-stable projection used for the snapshot id and the directory."""
        return {
            "entry_id": self.entry_id,
            "name": self.name,
            "source": self.source.value,
            "description": self.description,
            "risk_level": self.risk_level,
            "required_permissions": list(self.required_permissions),
            "catalog_mode": self.catalog_mode.value,
            "execution_mode": self.execution_mode.value,
            "input_signature": self.input_signature,
            "output_hint": self.output_hint,
        }

    def search_payload(self) -> dict[str, Any]:
        """Compact, prompt-safe result card. Never carries a full schema."""
        return {
            "id": self.entry_id,
            "name": self.name,
            "source": self.source.value,
            "description": _clip(self.description, DESCRIPTION_CHAR_MAX),
            "input": self.input_signature,
            "output": self.output_hint,
        }


def make_entry_id(tool: BaseTool, source: ToolSource) -> str:
    """Stable, collision-free id for *tool*.

    First-party tools are addressed by their own name, which is what the model
    already sees. MCP tools are namespaced by server so two servers cannot
    impersonate each other or a builtin (``mcp:<server>:<tool>``). Client
    tools are namespaced the same way.
    """
    name = getattr(tool, "name", "") or ""
    if source is ToolSource.MCP:
        server = get_mcp_source(tool) or {}
        return f"mcp:{server.get('server_name', 'unknown')}:{name}"
    if source is ToolSource.CLIENT:
        return f"client:{name}"
    return name


def build_entry(tool: BaseTool) -> CatalogEntry:
    """Build one :class:`CatalogEntry` from a real ``BaseTool``.

    Untrusted entries get ``input_schema=None`` and ``input_signature
    == "unknown"`` at construction time, so no later reader can accidentally
    index or render a schema that crossed an external boundary.
    """
    source = resolve_source(tool)
    trusted = not is_untrusted(source)
    name = getattr(tool, "name", "") or ""
    description = _clip(getattr(tool, "description", "") or "", DESCRIPTION_CHAR_MAX)
    input_schema = _first_party_input_schema(tool) if trusted else None
    output_schema = _trusted_output_schema(tool) if trusted else None
    mcp_server = (get_mcp_source(tool) or {}).get("server_name") if source is ToolSource.MCP else None
    return CatalogEntry(
        entry_id=make_entry_id(tool, source),
        name=name,
        source=source,
        description=description,
        risk_level=resolve_risk_level(tool),
        required_permissions=resolve_permissions(tool),
        catalog_mode=resolve_catalog_mode(tool),
        execution_mode=resolve_execution_mode(tool),
        input_signature=render_input_signature(input_schema) if trusted else UNKNOWN_INPUT,
        output_hint=render_output_hint(output_schema) if trusted else "",
        input_schema=input_schema,
        output_schema=output_schema,
        mcp_server=mcp_server,
        tool=tool,
    )


@dataclass(frozen=True)
class CatalogSnapshot:
    """Immutable, policy-filtered view of the catalog for one run."""

    snapshot_id: str
    entries: tuple[CatalogEntry, ...]
    excluded: tuple[str, ...] = ()
    policy_note: str = ""

    @property
    def size(self) -> int:
        return len(self.entries)

    @property
    def entry_ids(self) -> tuple[str, ...]:
        return tuple(entry.entry_id for entry in self.entries)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.entries)

    @property
    def source_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.source.value] = counts.get(entry.source.value, 0) + 1
        return counts

    @property
    def counter_scope(self) -> str:
        """Counter scope for a session that starts from this snapshot."""
        return compute_counter_scope(self.entry_ids)

    def by_id(self, entry_id: str) -> CatalogEntry | None:
        """Exact entry-id lookup (case-sensitive, as ids are canonical)."""
        return self._index().get(entry_id)

    def by_name(self, name: str) -> CatalogEntry | None:
        """Exact tool-name lookup.

        An exact tool name is always honored: the id is namespaced for untrusted
        sources, but a caller (or a model) that already knows the bare tool name
        must still resolve it.
        """
        return self._name_index().get(name)

    def resolve(self, selector: str) -> CatalogEntry | None:
        """Resolve an entry id, then a bare tool name. Never fuzzy."""
        selector = (selector or "").strip()
        if not selector:
            return None
        return self.by_id(selector) or self.by_name(selector)

    def sequential_entries(self) -> tuple[CatalogEntry, ...]:
        return tuple(entry for entry in self.entries if entry.sequential_only)

    def _index(self) -> dict[str, CatalogEntry]:
        cached = getattr(self, "_by_id", None)
        if cached is None:
            cached = {entry.entry_id: entry for entry in self.entries}
            object.__setattr__(self, "_by_id", cached)
        return cached

    def _name_index(self) -> dict[str, CatalogEntry]:
        cached = getattr(self, "_by_name", None)
        if cached is None:
            cached = {entry.name: entry for entry in self.entries}
            object.__setattr__(self, "_by_name", cached)
        return cached


def compute_snapshot_id(entries: Sequence[CatalogEntry]) -> str:
    """Content-addressed snapshot id.

    Derived from the public payload only, so it changes when the *catalog*
    changes and is byte-stable when nothing changed. Truncated to 16 hex chars,
    matching the existing ``DeferredToolCatalog.hash`` convention.
    """
    canonical = json.dumps([entry.public_payload() for entry in sorted(entries, key=lambda item: item.entry_id)], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_snapshot(
    tools: Iterable[BaseTool],
    *,
    allowed_names: set[str] | None = None,
    policy_note: str = "",
) -> CatalogSnapshot:
    """Build the effective, policy-filtered catalog snapshot for a run.

    ``allowed_names`` is the already-resolved skill/agent policy set (``None``
    means unrestricted, which is what
    :func:`alpha.skills.tool_policy.allowed_tool_names_for_skills` returns for
    legacy behavior). Denied tools are not merely hidden from search: they are
    absent from the snapshot, so no code path can call them through it.
    """
    entries: list[CatalogEntry] = []
    excluded: list[str] = []
    seen_ids: set[str] = set()
    for tool in tools:
        name = getattr(tool, "name", "") or ""
        if not name:
            continue
        if allowed_names is not None and name not in allowed_names:
            excluded.append(name)
            continue
        entry = build_entry(tool)
        if entry.catalog_mode is CatalogMode.DIRECT_ONLY:
            # Direct-only tools stay model-visible and are NOT added to the
            # catalog, so they cannot be compacted away or shadowed by search.
            excluded.append(name)
            continue
        if entry.entry_id in seen_ids:
            excluded.append(name)
            continue
        seen_ids.add(entry.entry_id)
        entries.append(entry)
    ordered = tuple(sorted(entries, key=lambda item: item.entry_id))
    return CatalogSnapshot(
        snapshot_id=compute_snapshot_id(ordered),
        entries=ordered,
        excluded=tuple(sorted(set(excluded))),
        policy_note=policy_note,
    )


def direct_only_tools(tools: Iterable[BaseTool], *, allowed_names: set[str] | None = None) -> list[BaseTool]:
    """The tools that stay directly visible: direct-only and still authorized.

    Used by the wiring patch to decide what the model keeps bound.
    """
    kept: list[BaseTool] = []
    for tool in tools:
        name = getattr(tool, "name", "") or ""
        if not name:
            continue
        if allowed_names is not None and name not in allowed_names:
            continue
        if resolve_catalog_mode(tool) is CatalogMode.DIRECT_ONLY:
            kept.append(tool)
    return kept
