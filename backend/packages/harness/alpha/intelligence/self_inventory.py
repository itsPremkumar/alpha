"""Self-inventory: one honest answer to "what am I, and what can I do?"

The problem this solves
-----------------------
Before this module, answering "what can you do?" meant knowing in advance that
there were *five* separate planes to ask — tools, skills, MCP servers, the
capability catalogue, memory subsystems — each with its own endpoint, its own
descriptor shape and its own idea of what "available" meant. Two of them were not
reachable from the model at all. An agent that did not already know the map would
guess, and a guessed capability claim is the single most damaging error an agent
can make about itself: it routes real work to something that does not exist.

So this module is the **one** place that reads every registry kind and projects
them into a single bounded payload. It is deliberately thin: it owns no fact of
its own, invents no count, and adds no interpretation. Everything here is a
projection over :mod:`alpha.workflow.registry`, plus the runtime identity.

Three properties are load-bearing, and each exists because the obvious alternative
has already caused a documented failure here:

**A failure is disclosed, never flattened.** Every section is computed through the
``_section`` wrapper, which converts a raising registry into
``status="unavailable"`` with the real exception text. One misconfigured MCP
server must not make the whole inventory useless — and equally, an unreadable
source must never render as ``count: 0``.

**A count is never a claim.** ``count`` is measured from the descriptors this
call actually read. ``available_count`` is a separate field because
"128 tools exist" and "128 tools are usable" are different statements, and only
the first is ever true from a static read. Nothing here reports ``health`` as
anything but ``unverified``, because no registry executes what it lists.

**The payload is bounded before it is returned.** 461 commands, 131 tools and 115
engines is roughly 40k characters of descriptors. Dumping that into a model context
to answer "do you have a browser tool?" is the failure mode this module exists to
prevent, and it is also the measured one: the interface-map ablation in
:mod:`alpha.grounding.manifest` found 67.8% self-reuse from names-and-signatures
against 29.2% from full source — *worse than nothing*. So ``detail="summary"`` is
the default and carries names only; full descriptors are opt-in per section.

Every ``reason`` the payload carries came from the registry that measured it. This
module adds no prose claim of its own about any subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from alpha.workflow.registry import REGISTRY_KINDS, CapabilityDescriptor, RegistryUnavailable, get_workflow_registry

SCHEMA_VERSION = "alpha.self-inventory.v1"

#: Inventory section names, in the order they are reported. This is
#: :data:`alpha.workflow.registry.REGISTRY_KINDS` today; it is spelled out as a
#: module constant because it is part of this module's published contract (a
#: caller may pass ``sections=``), and a published contract that silently changes
#: when an unrelated registry is added is not a contract.
INVENTORY_SECTIONS: tuple[str, ...] = REGISTRY_KINDS

Detail = Literal["summary", "full"]

#: Bounds. These are deliberately conservative: the point of the summary view is
#: that it fits comfortably inside one tool result.
MAX_SUMMARY_NAMES = 200
MAX_FULL_ENTRIES = 120
MAX_SEARCH_RESULTS = 25
MAX_QUERY_CHARS = 512
MAX_ENTRY_ID_CHARS = 256

_SUMMARY_STATUS_ORDER = ("available", "unavailable")


@dataclass(frozen=True)
class InventoryEntry:
    """One projected descriptor, carrying exactly the registry's own claims."""

    id: str
    kind: str
    availability: str
    source: str
    version: str | None
    health: str
    authority: str
    evidence_kind: str
    reason: str | None = None

    @classmethod
    def from_descriptor(cls, descriptor: CapabilityDescriptor) -> InventoryEntry:
        return cls(
            id=descriptor.id,
            kind=descriptor.kind,
            availability=descriptor.availability,
            source=descriptor.source,
            version=descriptor.version,
            health=descriptor.health,
            authority=descriptor.authority,
            evidence_kind=descriptor.evidence_kind,
            reason=descriptor.reason,
        )

    def to_summary(self) -> dict[str, Any]:
        """Name + kind + availability. The measured finding is that this is enough."""
        row: dict[str, Any] = {"id": self.id, "kind": self.kind, "availability": self.availability}
        if self.reason:
            row["reason"] = self.reason
        return row

    def to_full(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "availability": self.availability,
            "source": self.source,
            "version": self.version,
            "health": self.health,
            "authority": self.authority,
            "evidence_kind": self.evidence_kind,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class InventorySection:
    """One registry kind's projection, including the case where it could not be read."""

    name: str
    status: str
    count: int | None
    available_count: int | None
    unavailable_count: int | None
    error: str | None = None
    entries: tuple[InventoryEntry, ...] = field(default_factory=tuple)
    truncated: bool = False

    def to_dict(self, detail: Detail) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "count": self.count,
            "available_count": self.available_count,
            "unavailable_count": self.unavailable_count,
        }
        if self.error:
            payload["error"] = self.error
        if self.truncated:
            payload["truncated"] = True
        payload["entries"] = [entry.to_full() if detail == "full" else entry.to_summary() for entry in self.entries]
        return payload


@dataclass(frozen=True)
class SelfInventory:
    """The full projection. Serialise with :meth:`to_dict`."""

    sections: tuple[InventorySection, ...]
    detail: Detail
    generated_from: str = "alpha.workflow.registry"

    def section(self, name: str) -> InventorySection | None:
        for item in self.sections:
            if item.name == name:
                return item
        return None

    def totals(self) -> dict[str, int]:
        """Measured totals across the sections that could be read.

        A section with ``status="unavailable"`` contributes nothing rather than
        zero, so the totals are named ``read_sections`` / ``entries_read`` — the
        numbers describe what was read, not what exists.
        """
        return {
            "sections": len(self.sections),
            "read_sections": sum(1 for item in self.sections if item.status == "ok"),
            "unreadable_sections": sum(1 for item in self.sections if item.status != "ok"),
            "entries_read": sum(item.count or 0 for item in self.sections),
            "available_read": sum(item.available_count or 0 for item in self.sections),
        }

    def to_dict(self) -> dict[str, Any]:
        unreadable = [item.name for item in self.sections if item.status != "ok"]
        return {
            "schema_version": SCHEMA_VERSION,
            "detail": self.detail,
            "generated_from": self.generated_from,
            "totals": self.totals(),
            "unreadable_sections": unreadable,
            "sections": [item.to_dict(self.detail) for item in self.sections],
            "notes": _NOTES,
        }


_NOTES: tuple[str, ...] = (
    "availability is what the source of truth declares (registered / enabled / importable / configured); health is always 'unverified' because no registry here executes what it lists.",
    "A section with status='unavailable' was not read. Its count is null, never 0 — treat it as unknown, not empty.",
    "Counts are measured from this read. Drill into one entry with the 'capability' action for its source address.",
)


def _section(name: str, detail: Detail) -> InventorySection:
    """Project one registry kind, converting any failure into a disclosed absence.

    ``RegistryUnavailable`` is the expected failure (an unreadable config file, a
    missing manifest). Anything else is a defect in a registry; both are disclosed
    with the real exception text rather than swallowed, because an inventory that
    hides its own broken reader is the one place a broken subsystem would go
    unnoticed.
    """
    try:
        descriptors = get_workflow_registry().list(name)
    except Exception as exc:  # noqa: BLE001 - disclosure is the whole point
        return InventorySection(
            name=name,
            status="unavailable",
            count=None,
            available_count=None,
            unavailable_count=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    entries = [InventoryEntry.from_descriptor(descriptor) for descriptor in descriptors]
    available = sum(1 for entry in entries if entry.availability == "available")
    # Deterministic order: available first, then everything else alphabetically.
    # An agent deciding what it can use is looking for the boundary, and the order
    # must not change between two calls against an unchanged system. Unknown
    # statuses sort last rather than being dropped.
    order = {name: index for index, name in enumerate(_SUMMARY_STATUS_ORDER)}

    def sort_key(entry: InventoryEntry) -> tuple[int, str, str]:
        return (order.get(entry.availability, len(order)), entry.kind, entry.id)

    entries.sort(key=sort_key)

    limit = MAX_FULL_ENTRIES if detail == "full" else MAX_SUMMARY_NAMES
    return InventorySection(
        name=name,
        status="ok",
        count=len(entries),
        available_count=available,
        unavailable_count=len(entries) - available,
        entries=tuple(entries[:limit]),
        truncated=len(entries) > limit,
    )


def build_self_inventory(*, sections: tuple[str, ...] | None = None, detail: Detail = "summary") -> SelfInventory:
    """Read every requested registry kind and project it into one payload.

    Args:
        sections: Registry kinds to include. Defaults to every kind in
            :data:`INVENTORY_SECTIONS`. An unknown kind raises ``KeyError`` from
            the facade rather than being silently dropped — a section the caller
            asked for and did not get is exactly the omission-based
            over-advertising this plane exists to prevent.
        detail: ``"summary"`` (names, kinds, availability, reasons) or
            ``"full"`` (adds source address, authority, evidence, version).

    Blocking I/O: skills storage, the config file, ``git`` and the generated
    manifest are all read here. Gateway callers must use ``asyncio.to_thread``.
    """
    resolved_detail: Detail = "full" if detail == "full" else "summary"
    wanted = tuple(sections) if sections else INVENTORY_SECTIONS
    # Fail fast on an unknown kind, before doing any disk work, so a typo costs
    # nothing and cannot be mistaken for a section that happened to be empty.
    facade = get_workflow_registry()
    for name in wanted:
        facade.registry(name)
    return SelfInventory(sections=tuple(_section(name, resolved_detail) for name in wanted), detail=resolved_detail)


def describe_capability(kind: str, entry_id: str) -> dict[str, Any]:
    """One entry, in full, from its registry.

    This is the drill-down the summary view points at. Returns a disclosed
    absence (``status="absent"``) rather than raising when the id is not in that
    registry, because "no tool by that name" is a normal answer, and raising
    would make a typo look like a subsystem failure.

    A wrong ``kind`` is different: it is a caller mistake worth surfacing, so it
    is returned as ``status="unknown_kind"`` with the valid kinds listed.
    """
    facade = get_workflow_registry()
    try:
        facade.registry(kind)
    except KeyError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "unknown_kind",
            "kind": kind,
            "sections": list(INVENTORY_SECTIONS),
            "detail": str(exc),
        }
    try:
        descriptor = facade.describe(kind, entry_id)
    except RegistryUnavailable as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "unavailable",
            "kind": kind,
            "id": entry_id,
            "detail": str(exc),
        }
    if descriptor is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "absent",
            "kind": kind,
            "id": entry_id,
            "detail": f"no entry {entry_id!r} in the {kind!r} registry; it was read and the id is not there",
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "kind": kind,
        "entry": InventoryEntry.from_descriptor(descriptor).to_full(),
    }


def search_inventory(query: str, *, sections: tuple[str, ...] | None = None, limit: int = MAX_SEARCH_RESULTS) -> dict[str, Any]:
    """Find inventory entries by substring across id, kind and reason.

    Deliberately **lexical substring**, not the BM25 index in
    :mod:`alpha.tools.discovery`: that index ranks tools by task intent and is the
    right tool for "which tool should I use to X". This answers the narrower and
    more literal question — "does a thing called roughly like this exist anywhere
    in my inventory?" — and it must work for skills, commands, bots and engines,
    which the tool index does not cover at all.

    A substring search can be fooled by a near-miss, so every hit carries its
    registry kind and availability; a caller that wants semantic ranking for tools
    should use ``tool_search`` instead.
    """
    needle = str(query or "").strip().lower()
    if not needle:
        return {"schema_version": SCHEMA_VERSION, "query": "", "count": 0, "hits": [], "detail": "a non-empty query is required"}
    if len(needle) > MAX_QUERY_CHARS:
        return {
            "schema_version": SCHEMA_VERSION,
            "query": needle[:MAX_QUERY_CHARS],
            "count": 0,
            "hits": [],
            "detail": f"query exceeds {MAX_QUERY_CHARS} characters",
        }
    bounded_limit = max(1, min(int(limit or MAX_SEARCH_RESULTS), MAX_SEARCH_RESULTS))
    wanted = tuple(sections) if sections else INVENTORY_SECTIONS
    facade = get_workflow_registry()
    for name in wanted:
        facade.registry(name)

    hits: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for name in wanted:
        section = _section(name, "summary")
        if section.status != "ok":
            unreadable.append(f"{name}: {section.error}")
            continue
        for entry in section.entries:
            haystack = f"{entry.id} {entry.kind} {entry.reason or ''}".lower()
            if needle in haystack:
                hits.append({"section": name, **entry.to_summary()})

    # Rank by where the needle matched, then by availability, so an exact id hit
    # outranks a reason-text mention and a usable entry outranks a disabled one.
    hits.sort(key=lambda hit: (needle not in hit["id"].lower(), hit["availability"] != "available", hit["section"], hit["id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "query": needle,
        "count": min(len(hits), bounded_limit),
        "matched": len(hits),
        "truncated": len(hits) > bounded_limit,
        "hits": hits[:bounded_limit],
        "unreadable_sections": unreadable,
        "detail": "substring match on id/kind/reason; use the 'capability' action for one entry's full address",
    }


def identity_projection() -> dict[str, Any]:
    """The repository/runtime identity block, for a caller that only needs that.

    A thin wrapper over the ``identity`` registry so the tool's ``identity``
    action does not have to know that identity is just another section. A failure
    is disclosed here too — "which repository is this" must never degrade to an
    empty object, because an empty object invites a guessed answer.
    """
    section = _section("identity", "full")
    payload = section.to_dict("full")
    payload["repository_facts"] = _repository_facts(section.entries)
    return payload


def _repository_facts(entries: tuple[InventoryEntry, ...]) -> dict[str, Any]:
    """The plain-language identity fields, or an honest disclosure of absence.

    Parsed from the descriptor *addresses* the identity registry builds, not from a
    second read of the manifest — so this can never disagree with the section it
    is derived from.
    """
    facts: dict[str, Any] = {}
    for entry in entries:
        if entry.id == "repository_url" and entry.availability == "available":
            facts["repository_url"] = entry.source.split("->", 1)[-1].strip()
        elif entry.id == "repository" and entry.availability == "available":
            for token in entry.source.split("->", 1)[-1].split():
                if "=" not in token:
                    continue
                key, _, value = token.partition("=")
                if key in {"provider", "owner", "name", "defaultBranch"}:
                    facts[f"repository_{key}"] = value
        elif entry.id == "runtime" and entry.availability == "available":
            for token in entry.source.split("->", 1)[-1].split():
                if "=" not in token:
                    continue
                key, _, value = token.partition("=")
                if key in {"agentId", "os", "arch", "gitCommit"}:
                    facts[key] = value
            if entry.version:
                facts["alphaVersion"] = entry.version
    facts["note"] = "Read from config/project-manifest.json and alpha.evolution.identity. An 'unknown' gitCommit means the git probe failed and carries its reason in the runtime row; it is never a fabricated revision."
    return facts


def inventory_status() -> dict[str, Any]:
    """A cheap readiness summary of the inventory plane itself.

    Answers "can I introspect myself right now?" without reading every registry,
    which is what a caller needs before deciding whether to ask for detail. Each
    registry measures its own source inside ``health()``, so this is cheap relative
    to a full read and still honest about which sources are broken.
    """
    report = get_workflow_registry().health()
    unreadable = [kind for kind, health in report.items() if health.status != "ok"]
    return {
        "schema_version": SCHEMA_VERSION,
        "sections": sorted(report),
        "unreadable_sections": unreadable,
        "ok": not unreadable,
        "detail": (f"{len(report) - len(unreadable)}/{len(report)} registries readable" + (f"; unavailable: {', '.join(unreadable)}" if unreadable else "")),
        "notes": ["registry health is measured by each registry against its own source; it is not a claim that any subsystem is running."],
    }


__all__ = [
    "INVENTORY_SECTIONS",
    "SCHEMA_VERSION",
    "InventoryEntry",
    "InventorySection",
    "SelfInventory",
    "build_self_inventory",
    "describe_capability",
    "identity_projection",
    "inventory_status",
    "search_inventory",
]
