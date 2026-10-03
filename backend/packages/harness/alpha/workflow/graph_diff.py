"""Structural diff between two workflow graph revisions.

Why this exists
---------------
`PlanGraphStore` already keeps every graph revision of a workflow as an
immutable, CAS-guarded record, and a `WorkflowPatch` already records *which*
operations produced a revision. What neither of them answered was the question
an operator actually asks after a replan: **what is different about the plan
now compared with then?** Reading two `v*.json` files and eyeballing them is
not an answer, and a UI that shows "revision 4" with no diff is showing a
number, not a history.

Two properties make this worth its own module rather than a loop in a router:

* **Structural change and runtime state are different facts.** A node's
  ``type``/``executor``/``retry_policy``/``depends_on`` are *plan intent*; a
  node's ``status``/``output``/``evidence``/``tokens_consumed`` are what
  happened while running it. A revision captured mid-run carries both, and
  reporting "node ``build`` changed" because its status moved from ``running``
  to ``succeeded`` would be a lie about the plan. Every change is therefore
  tagged with its own kind, and :attr:`GraphDiff.plan_changes` /
  :attr:`GraphDiff.runtime_changes` split them without ever dropping either —
  nothing is hidden, the caller chooses the lens.
* **A diff can leak.** Node ``config`` routinely holds endpoints, headers and
  credentials-shaped values, and node ``output`` is whatever a tool printed.
  Every rendered payload passes through the workflow dispatcher's redactor, so
  a diff endpoint cannot become the side door that a redacted event log
  already closes.

Honesty rules
-------------
* The diff is a **pure function over two snapshots** — it never reads the live
  engine, never touches a store, and never infers a reason for a change. The
  *reason* lives on the `PlanVersion` (`note` + `source`); :func:`diff_plan_versions`
  attaches it, and it stays ``""`` when nobody recorded one rather than being
  invented.
* Comparison is **exhaustive over every model field**: a field that is missing
  from this module's field lists is a field that would silently not diff, so
  both lists are derived from the models themselves and a test pins them
  against `WorkflowNode`'s own ``model_fields``.
* Output is **bounded and deterministic**: values are length-capped with an
  explicit truncation marker, and changes are emitted in a stable order so the
  same two revisions always produce byte-identical JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from alpha.workflow.events import redact_event_payload
from alpha.workflow.models import WorkflowEdge, WorkflowGraph, WorkflowNode

__all__ = [
    "ChangeKind",
    "FieldChange",
    "GraphChange",
    "GraphDiff",
    "NODE_RUNTIME_FIELDS",
    "NODE_STRUCTURAL_FIELDS",
    "diff_graphs",
    "diff_plan_versions",
    "edge_key",
]

#: Fields that describe the *plan* — what the workflow intends to do. Changing
#: one of these between revisions is a real change of plan.
NODE_STRUCTURAL_FIELDS: tuple[str, ...] = (
    "type",
    "executor",
    "idempotency_key",
    "config",
    "prompt",
    "category",
    "condition",
    "timeout_seconds",
    "retry_policy",
    "loop_policy",
    "write_scope",
    "requires_approval",
    "gate_timeout_seconds",
    "compensation_node_id",
    "budget",
    "depends_on",
)

#: Fields that describe what *happened* to a node while it ran. They change on
#: every execution and must never be reported as a change of plan.
NODE_RUNTIME_FIELDS: tuple[str, ...] = (
    "status",
    "evidence",
    "output",
    "tokens_consumed",
    "approval_request_id",
    "approval_requested_at",
)

#: Every declarative field of `WorkflowEdge` (the model carries no runtime state).
EDGE_FIELDS: tuple[str, ...] = ("condition", "priority", "mode")

# A diff value longer than this is truncated with an explicit marker: node
# output is unbounded and an unbounded diff is not renderable or transmissible.
_MAX_VALUE_CHARS = 2000
_PREVIEW_CHARS = 240


class ChangeKind(StrEnum):
    """What kind of difference a `GraphChange` represents."""

    NODE_ADDED = "node_added"
    NODE_REMOVED = "node_removed"
    NODE_CHANGED = "node_changed"
    #: Same node id, but only runtime-state fields differ — NOT a plan change.
    NODE_STATE_CHANGED = "node_state_changed"
    EDGE_ADDED = "edge_added"
    EDGE_REMOVED = "edge_removed"
    METADATA_CHANGED = "metadata_changed"


#: Plan-intent kinds. A replan/patch produces these; a re-run produces none.
PLAN_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.NODE_ADDED,
        ChangeKind.NODE_REMOVED,
        ChangeKind.NODE_CHANGED,
        ChangeKind.EDGE_ADDED,
        ChangeKind.EDGE_REMOVED,
        ChangeKind.METADATA_CHANGED,
    }
)

_KIND_ORDER: tuple[ChangeKind, ...] = (
    ChangeKind.NODE_ADDED,
    ChangeKind.NODE_REMOVED,
    ChangeKind.NODE_CHANGED,
    ChangeKind.NODE_STATE_CHANGED,
    ChangeKind.EDGE_ADDED,
    ChangeKind.EDGE_REMOVED,
    ChangeKind.METADATA_CHANGED,
)


def _bound(value: Any) -> Any:
    """Return a JSON-safe, length-bounded copy of a diff value.

    Strings longer than the ceiling are cut with a marker that states the true
    length — truncating silently would make a diff look complete when it is
    not. Containers are walked and bounded per element; the container's own
    identity is preserved so a dict still reads as a dict.
    """
    if isinstance(value, str):
        if len(value) <= _MAX_VALUE_CHARS:
            return value
        total = len(value)
        omitted = total - _PREVIEW_CHARS
        return f"{value[:_PREVIEW_CHARS]}…<truncated: {total} chars total, {omitted} omitted>"
    if isinstance(value, dict):
        return {str(k): _bound(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bound(item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _bound(str(value))


@dataclass(frozen=True)
class FieldChange:
    """One field that differs between the two revisions."""

    field: str
    before: Any
    after: Any
    structural: bool

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "before": _bound(self.before), "after": _bound(self.after), "structural": self.structural}


@dataclass(frozen=True)
class GraphChange:
    """One difference between two graph revisions."""

    kind: ChangeKind
    #: Node id for node kinds, ``"source->target"`` for edge kinds, the metadata
    #: key for `METADATA_CHANGED`.
    key: str
    fields: tuple[FieldChange, ...] = ()

    @property
    def structural(self) -> bool:
        """True when this change altered the plan rather than runtime state."""
        return self.kind in PLAN_KINDS

    @property
    def summary(self) -> str:
        """One human line, built from measured facts only."""
        if self.kind is ChangeKind.NODE_ADDED:
            return f"node added: {self.key}"
        if self.kind is ChangeKind.NODE_REMOVED:
            return f"node removed: {self.key}"
        if self.kind is ChangeKind.EDGE_ADDED:
            return f"edge added: {self.key}"
        if self.kind is ChangeKind.EDGE_REMOVED:
            return f"edge removed: {self.key}"
        if self.kind is ChangeKind.METADATA_CHANGED:
            return f"metadata changed: {self.key}"
        names = ", ".join(item.field for item in self.fields) or "(no field detail)"
        if self.kind is ChangeKind.NODE_STATE_CHANGED:
            return f"node {self.key} runtime state changed: {names}"
        return f"node {self.key} changed: {names}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "key": self.key,
            "structural": self.structural,
            "summary": self.summary,
            "fields": [item.to_dict() for item in self.fields],
        }


@dataclass(frozen=True)
class GraphDiff:
    """The complete difference between two `WorkflowGraph` revisions."""

    base_version: int
    target_version: int
    changes: tuple[GraphChange, ...] = ()
    #: Attached only when the caller supplied the revisions' provenance
    #: (`PlanVersion.note` / `source`); stays ``""``/``None`` when unknown so a
    #: missing reason reads as missing rather than as a default.
    reason: str = ""
    source: str | None = None
    base_node_count: int = 0
    target_node_count: int = 0
    base_edge_count: int = 0
    target_edge_count: int = 0

    @property
    def identical(self) -> bool:
        return not self.changes

    @property
    def plan_changes(self) -> tuple[GraphChange, ...]:
        """Changes to plan intent (adds, removes, structural edits)."""
        return tuple(change for change in self.changes if change.structural)

    @property
    def runtime_changes(self) -> tuple[GraphChange, ...]:
        """Changes to node runtime state only — not a change of plan."""
        return tuple(change for change in self.changes if not change.structural)

    @property
    def has_plan_change(self) -> bool:
        return any(change.structural for change in self.changes)

    @property
    def summary(self) -> dict[str, int]:
        """Counts per kind, keyed by every kind so a zero is visible as a zero."""
        counts = {kind.value: 0 for kind in _KIND_ORDER}
        for change in self.changes:
            counts[change.kind.value] += 1
        return counts

    @property
    def affected_nodes(self) -> list[str]:
        """Sorted, de-duplicated node ids touched by any change."""
        touched: set[str] = set()
        for change in self.changes:
            if change.kind in (ChangeKind.NODE_ADDED, ChangeKind.NODE_REMOVED, ChangeKind.NODE_CHANGED, ChangeKind.NODE_STATE_CHANGED):
                touched.add(change.key)
        return sorted(touched)

    def to_dict(self, *, include_runtime: bool = True) -> dict[str, Any]:
        """Render a JSON-safe, credential-redacted representation.

        Redaction is applied at render time so no caller can forget it on the
        way out of an endpoint.
        """
        selected = self.changes if include_runtime else self.plan_changes
        payload: dict[str, Any] = {
            "base_version": self.base_version,
            "target_version": self.target_version,
            "identical": self.identical,
            "has_plan_change": self.has_plan_change,
            "reason": self.reason,
            "source": self.source,
            "summary": self.summary,
            "counts": {
                "base_nodes": self.base_node_count,
                "target_nodes": self.target_node_count,
                "base_edges": self.base_edge_count,
                "target_edges": self.target_edge_count,
                "plan_changes": len(self.plan_changes),
                "runtime_changes": len(self.runtime_changes),
            },
            "affected_nodes": self.affected_nodes,
            "changes": [change.to_dict() for change in selected],
        }
        return redact_event_payload(payload)


def edge_key(edge: WorkflowEdge, *, occurrence: int = 0) -> str:
    """Stable identity for an edge.

    ``source->target`` is the natural key; a graph may legitimately carry more
    than one edge between the same pair (different conditions/priorities), so
    repeats are disambiguated by their order of appearance rather than being
    collapsed into one — collapsing would hide a real added/removed edge.
    """
    base = f"{edge.source}->{edge.target}"
    return base if occurrence == 0 else f"{base}#{occurrence}"


def _edge_keys(edges: list[WorkflowEdge]) -> dict[str, WorkflowEdge]:
    seen: dict[str, int] = {}
    keyed: dict[str, WorkflowEdge] = {}
    for edge in edges:
        base = f"{edge.source}->{edge.target}"
        occurrence = seen.get(base, 0)
        seen[base] = occurrence + 1
        keyed[edge_key(edge, occurrence=occurrence)] = edge
    return keyed


def _diff_node(base: WorkflowNode, target: WorkflowNode) -> tuple[FieldChange, ...]:
    structural: list[FieldChange] = []
    runtime: list[FieldChange] = []
    for name in NODE_STRUCTURAL_FIELDS:
        before = getattr(base, name)
        after = getattr(target, name)
        if before != after:
            structural.append(FieldChange(field=name, before=before, after=after, structural=True))
    for name in NODE_RUNTIME_FIELDS:
        before = getattr(base, name)
        after = getattr(target, name)
        if before != after:
            runtime.append(FieldChange(field=name, before=before, after=after, structural=False))
    # Structural first, then runtime, each alphabetically: deterministic output.
    structural.sort(key=lambda item: item.field)
    runtime.sort(key=lambda item: item.field)
    return tuple(structural + runtime)


def _node_kind(structural_differ: bool, runtime_differ: bool) -> ChangeKind:
    if structural_differ:
        return ChangeKind.NODE_CHANGED
    if runtime_differ:
        return ChangeKind.NODE_STATE_CHANGED
    raise AssertionError("a node change was produced with no differing field")


def diff_graphs(
    base: WorkflowGraph,
    target: WorkflowGraph,
    *,
    reason: str = "",
    source: str | None = None,
) -> GraphDiff:
    """Compare two graph revisions, oldest-first by convention.

    Direction matters: ``diff_graphs(v3, v1)`` describes going *backwards* and
    will report a node added by v3 as *removed*. Nothing is reordered or
    symmetrised, because an operator comparing two revisions wants to know what
    changed between the two they named, in the order they named them.
    """
    changes: list[GraphChange] = []

    base_nodes = base.nodes
    target_nodes = target.nodes

    for node_id in sorted(set(target_nodes) - set(base_nodes)):
        changes.append(GraphChange(kind=ChangeKind.NODE_ADDED, key=node_id))

    for node_id in sorted(set(base_nodes) - set(target_nodes)):
        changes.append(GraphChange(kind=ChangeKind.NODE_REMOVED, key=node_id))

    for node_id in sorted(set(base_nodes) & set(target_nodes)):
        field_changes = _diff_node(base_nodes[node_id], target_nodes[node_id])
        if not field_changes:
            continue
        structural_differ = any(item.structural for item in field_changes)
        runtime_differ = any(not item.structural for item in field_changes)
        changes.append(GraphChange(kind=_node_kind(structural_differ, runtime_differ), key=node_id, fields=field_changes))

    base_edges = _edge_keys(base.edges)
    target_edges = _edge_keys(target.edges)

    for key in sorted(set(target_edges) - set(base_edges)):
        changes.append(GraphChange(kind=ChangeKind.EDGE_ADDED, key=key))

    for key in sorted(set(base_edges) - set(target_edges)):
        changes.append(GraphChange(kind=ChangeKind.EDGE_REMOVED, key=key))

    for key in sorted(set(base_edges) & set(target_edges)):
        before_edge = base_edges[key]
        after_edge = target_edges[key]
        field_changes = tuple(FieldChange(field=name, before=getattr(before_edge, name), after=getattr(after_edge, name), structural=True) for name in EDGE_FIELDS if getattr(before_edge, name) != getattr(after_edge, name))
        if field_changes:
            changes.append(GraphChange(kind=ChangeKind.EDGE_ADDED, key=key, fields=field_changes))

    base_meta = base.metadata or {}
    target_meta = target.metadata or {}
    for meta_key in sorted(set(base_meta) | set(target_meta)):
        before = base_meta.get(meta_key)
        after = target_meta.get(meta_key)
        if before == after:
            continue
        changes.append(
            GraphChange(
                kind=ChangeKind.METADATA_CHANGED,
                key=meta_key,
                fields=(FieldChange(field=meta_key, before=before, after=after, structural=True),),
            )
        )

    # Stable ordering: kind first (in declaration order), then key. Sorting at
    # the end rather than while building keeps each section readable above and
    # still guarantees byte-identical output for identical inputs.
    changes.sort(key=lambda change: (_KIND_ORDER.index(change.kind), change.key))

    return GraphDiff(
        base_version=base.version,
        target_version=target.version,
        changes=tuple(changes),
        reason=reason,
        source=source,
        base_node_count=len(base.nodes),
        target_node_count=len(target.nodes),
        base_edge_count=len(base.edges),
        target_edge_count=len(target.edges),
    )


def diff_plan_versions(base: Any, target: Any) -> GraphDiff:
    """Diff two `PlanVersion` records, attaching their recorded provenance.

    The reason and source are read from the *target* revision — that is the
    revision someone created, so it is the one whose note explains it. A
    revision recorded with an empty note reports ``reason=""``: the absence of
    a reason is a fact about the record, and inventing one would defeat the
    point of requiring one.
    """
    return diff_graphs(
        base.graph,
        target.graph,
        reason=str(getattr(target, "note", "") or ""),
        source=str(getattr(target, "source", "") or "") or None,
    )
