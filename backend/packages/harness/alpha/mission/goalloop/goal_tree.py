"""The goal tree: hierarchical goals, each with its own lifecycle and evidence.

**Why this exists.** The run loop used to carry a goal as one objective string
plus a flat ``subgoals: list[str]``. A flat list has no identity, no state and no
evidence, so "half the work is done" was unrepresentable and a sub-goal could not
fail without the whole goal failing. This module gives every level of a
decomposition its own objective, its own success criteria, its own state and its
own append-only evidence log, and it gives the run loop a *walk* rather than a
single verdict.

**Four properties are load-bearing.**

1. **``COMPLETED`` is unreachable without measured evidence.** An unmeasured
   criterion is not a passing criterion. ``None`` and ``False`` are different
   facts, and collapsing them is how a goal closes on a check nobody ran. This is
   the same gate ``alpha.apex.goals`` uses; it is ported here rather than
   imported, because ``apex`` imports ``mission`` and a ``mission`` module
   importing ``apex`` back would invert that layering.
2. **A child cannot outrank an ancestor.** A decomposition that reorders the work
   it was derived from is a policy break, so it is refused at creation.
3. **The walk terminates.** Depth, fan-out and repeated childless decomposition
   are all bounded; a node that cannot produce children is forced to execute.
4. **Completion flows up honestly.** A parent whose children have all finished
   reports ``COMPLETED`` / ``PARTIAL`` / ``FAILED`` from *its own* evidence, and a
   parent whose criteria were never measured parks in ``VERIFYING`` rather than
   being declared failed or waved through.

**On rule 4 of the design's walker.** The design says a child is runnable only
once its parent is ``COMPLETED``. Read literally that makes decomposition
dead-on-arrival: a parent moves to ``EXECUTING`` *because* it is now working
through its children, so its children could never run. What is implemented here
is the intent behind the rule - a child does not run behind a parent that is
``BLOCKED``, ``FAILED``, ``CANCELLED`` or already ``COMPLETED``, and a child
behind a ``PARTIAL`` parent runs only if it opts in. The deviation is called out
because it is a behavioural difference from the written spec, not an
implementation detail.

**Layering.** This module imports nothing from ``alpha.apex``: ``apex`` is the
outer control plane and already imports ``mission.acceptance`` and
``mission.lifecycle``, so importing ``apex`` from here would create a package
cycle. ``GoalEvidence`` is therefore a local port of the APEX model, same fields
and same semantics.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Final

__all__ = [
    "ACTIVE_NODE_STATES",
    "MAX_ANCESTOR_DEPTH",
    "MAX_CHILDREN_PER_NODE",
    "MAX_DECOMPOSITION_ATTEMPTS",
    "MAX_TREE_DEPTH",
    "NODE_TRANSITIONS",
    "RUNNABLE_NODE_STATES",
    "TERMINAL_NODE_STATES",
    "GoalEvidence",
    "GoalNode",
    "GoalTree",
    "IllegalNodeTransition",
    "NodeState",
    "new_node_id",
]


class NodeState(StrEnum):
    """The twelve states of one goal node.

    Simpler than APEX's fifteen on purpose: there is no ``PLANNING`` (compiling a
    plan is an implementation detail of ``EXECUTING``, tracked by
    ``plan_version``), and no ``WAITING``/``PAUSED`` pair (a parked node is
    ``BLOCKED`` with a reason; the run loop's own ``WAIT``/``PAUSED`` actions
    already own that vocabulary at the loop level).
    """

    IDLE = "idle"
    ANALYZING = "analyzing"
    DECOMPOSING = "decomposing"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    REPLANNING = "replanning"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_NODE_STATES

    @property
    def is_active(self) -> bool:
        """Work is expected to progress without an external event."""

        return self in ACTIVE_NODE_STATES

    @property
    def is_runnable(self) -> bool:
        """The walker may select this node.

        ``IDLE`` is included deliberately: a node that has not started is exactly
        what the walker must hand the run loop so the loop can start it. ``RECOVERING``
        is excluded because recovery is driven by the node's own repair, not by the
        walk, and including it would let the walker re-enter execution before the
        repair it is recovering from has been planned.
        """

        return self in RUNNABLE_NODE_STATES


#: Terminal states. ``PARTIAL`` is one of them: a partial result is a real
#: outcome that gets recorded and surfaced, not an error to be retried into
#: either success or total failure.
TERMINAL_NODE_STATES: frozenset[NodeState] = frozenset(
    {
        NodeState.COMPLETED,
        NodeState.PARTIAL,
        NodeState.FAILED,
        NodeState.CANCELLED,
    }
)

#: States in which work is expected to progress without an external event.
ACTIVE_NODE_STATES: frozenset[NodeState] = frozenset(
    {
        NodeState.ANALYZING,
        NodeState.DECOMPOSING,
        NodeState.EXECUTING,
        NodeState.VERIFYING,
        NodeState.RECOVERING,
        NodeState.REPLANNING,
    }
)

#: States the walker will select.
RUNNABLE_NODE_STATES: frozenset[NodeState] = frozenset(
    {
        NodeState.IDLE,
        NodeState.ANALYZING,
        NodeState.DECOMPOSING,
        NodeState.EXECUTING,
        NodeState.VERIFYING,
        NodeState.REPLANNING,
    }
)

#: States in which an internal node works *through* its children rather than
#: through a plan of its own. The walker prefers such a node's children.
_CHILD_DRIVEN_STATES: frozenset[NodeState] = frozenset({NodeState.EXECUTING, NodeState.DECOMPOSING})

#: A parent in one of these states gates its whole subtree.
_GATING_STATES: frozenset[NodeState] = frozenset({NodeState.BLOCKED, NodeState.FAILED, NodeState.CANCELLED, NodeState.COMPLETED})

#: Legal transitions. Two rules shape this table:
#:
#: * ``VERIFYING`` can go back to ``EXECUTING``. A node whose verification found
#:   unmeasured criteria re-enters execution to measure them rather than dying.
#: * ``BLOCKED`` reaches ``REPLANNING`` and ``EXECUTING``. Being stopped is not a
#:   dead end.
NODE_TRANSITIONS: dict[NodeState, frozenset[NodeState]] = {
    NodeState.IDLE: frozenset({NodeState.ANALYZING, NodeState.DECOMPOSING, NodeState.EXECUTING, NodeState.VERIFYING, NodeState.COMPLETED, NodeState.BLOCKED, NodeState.FAILED, NodeState.CANCELLED}),
    NodeState.ANALYZING: frozenset(
        {
            NodeState.DECOMPOSING,
            NodeState.EXECUTING,
            NodeState.BLOCKED,
            NodeState.FAILED,
            NodeState.CANCELLED,
        }
    ),
    NodeState.DECOMPOSING: frozenset(
        {
            NodeState.EXECUTING,
            NodeState.REPLANNING,
            NodeState.BLOCKED,
            NodeState.FAILED,
            NodeState.CANCELLED,
        }
    ),
    NodeState.EXECUTING: frozenset(
        {
            NodeState.VERIFYING,
            NodeState.COMPLETED,
            NodeState.PARTIAL,
            NodeState.RECOVERING,
            NodeState.REPLANNING,
            NodeState.BLOCKED,
            NodeState.FAILED,
            NodeState.CANCELLED,
        }
    ),
    NodeState.VERIFYING: frozenset(
        {
            NodeState.COMPLETED,
            NodeState.PARTIAL,
            NodeState.EXECUTING,
            NodeState.RECOVERING,
            NodeState.REPLANNING,
            NodeState.BLOCKED,
            NodeState.FAILED,
            NodeState.CANCELLED,
        }
    ),
    NodeState.RECOVERING: frozenset(
        {
            NodeState.EXECUTING,
            NodeState.REPLANNING,
            NodeState.VERIFYING,
            NodeState.BLOCKED,
            NodeState.FAILED,
            NodeState.CANCELLED,
        }
    ),
    NodeState.REPLANNING: frozenset(
        {
            NodeState.DECOMPOSING,
            NodeState.EXECUTING,
            NodeState.BLOCKED,
            NodeState.FAILED,
            NodeState.CANCELLED,
        }
    ),
    NodeState.BLOCKED: frozenset(
        {
            NodeState.ANALYZING,
            NodeState.EXECUTING,
            NodeState.REPLANNING,
            NodeState.FAILED,
            NodeState.CANCELLED,
        }
    ),
    NodeState.COMPLETED: frozenset(),
    NodeState.PARTIAL: frozenset(),
    NodeState.FAILED: frozenset(),
    NodeState.CANCELLED: frozenset(),
}

#: Ancestors a node may not exceed, by walking ``parent_id``. The bound is not
#: defensive noise: a cycle introduced by a bad write would otherwise hang every
#: reader that walks ancestry.
MAX_ANCESTOR_DEPTH: Final[int] = 12

#: Bounds that keep the tree, and therefore the walk, finite.
MAX_TREE_DEPTH: Final[int] = 4
MAX_CHILDREN_PER_NODE: Final[int] = 7
MAX_DECOMPOSITION_ATTEMPTS: Final[int] = 2


class IllegalNodeTransition(ValueError):
    """Raised when a node is asked to move somewhere it cannot go."""


def new_node_id() -> str:
    return f"gn-{uuid.uuid4().hex[:10]}"


@dataclass
class GoalEvidence:
    """One measured observation attached to a node.

    ``holds`` is ``None`` until something measured it. ``None`` and ``False`` are
    different facts: "nobody checked" is not "it did not hold", and collapsing
    them is how a node closes on a criterion nobody ran.
    """

    evidence_id: str
    criterion: str = ""
    source: str = ""
    detail: str = ""
    holds: bool | None = None
    recorded_at: float = field(default_factory=time.time)

    @classmethod
    def unmeasured(cls, criterion: str, *, source: str = "", detail: str = "") -> GoalEvidence:
        return cls(evidence_id=f"ev-{uuid.uuid4().hex[:8]}", criterion=criterion, source=source, detail=detail)

    @classmethod
    def measured(cls, criterion: str, holds: bool, *, source: str = "", detail: str = "") -> GoalEvidence:
        return cls(
            evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
            criterion=criterion,
            source=source,
            detail=detail,
            holds=bool(holds),
        )

    @property
    def evaluated(self) -> bool:
        return self.holds is not None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evaluated"] = self.evaluated
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GoalEvidence:
        holds = data.get("holds")
        return cls(
            evidence_id=str(data.get("evidence_id", "")),
            criterion=str(data.get("criterion", "")),
            source=str(data.get("source", "")),
            detail=str(data.get("detail", "")),
            holds=None if holds is None else bool(holds),
            recorded_at=float(data.get("recorded_at", 0.0) or 0.0),
        )


@dataclass
class GoalNode:
    """One objective with the criteria that would prove it done."""

    node_id: str
    parent_id: str
    objective: str
    success_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    #: Higher wins. A child may not outrank any ancestor.
    priority: int = 50
    state: NodeState = NodeState.IDLE
    evidence: list[GoalEvidence] = field(default_factory=list)
    #: Which plan revision this node is executing. Bumped by ``REPLANNING``.
    plan_version: int = 0
    #: Link into the versioned plan store (``alpha.goals``), when there is one.
    plan_id: str = ""
    child_ids: list[str] = field(default_factory=list)
    session_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    #: Consecutive decompositions that produced no children. Bounded by
    #: :data:`MAX_DECOMPOSITION_ATTEMPTS`, after which the node is forced to execute.
    decompose_attempts: int = 0
    #: How many times this node has been replanned.
    replan_count: int = 0
    #: The real blocker, or ``""``.
    blocked_reason: str = ""
    #: Whether this node may run behind a ``PARTIAL`` parent.
    can_run_despite_partial_parent: bool = False

    # -- derived ------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal

    @property
    def is_active(self) -> bool:
        return self.state.is_active

    @property
    def is_leaf(self) -> bool:
        return not self.child_ids

    def latest_evidence(self) -> dict[str, GoalEvidence]:
        """The most recent measurement per criterion.

        The ``evidence`` list stays append-only because the audit trail is the
        point; the *current verdict* for a criterion is its latest measurement.
        Scoping the failure check to the whole log instead would mean a node that
        failed once could never complete, even after a passing re-verification -
        a node permanently poisoned by its own history.
        """

        latest: dict[str, GoalEvidence] = {}
        for item in self.evidence:
            if item.evaluated and item.criterion:
                latest[item.criterion] = item
        return latest

    def criteria_without_evidence(self) -> list[str]:
        """Criteria whose latest measurement is still unmeasured.

        An empty list is what completion requires.
        """

        measured = self.latest_evidence()
        return [criterion for criterion in self.success_criteria if criterion not in measured]

    def criteria_failed(self) -> list[str]:
        """Criteria whose latest measurement did not hold."""

        return [criterion for criterion, evidence in self.latest_evidence().items() if evidence.holds is False]

    def criteria_passed(self) -> list[str]:
        return [criterion for criterion, evidence in self.latest_evidence().items() if evidence.holds is True]

    def acceptance_evidence(self) -> dict[str, bool]:
        """Latest measured booleans, keyed by criterion.

        Only evaluated evidence appears. A criterion with no entry stays
        unmeasured, which is the point.
        """

        return {criterion: bool(evidence.holds) for criterion, evidence in self.latest_evidence().items()}

    def refusal_detail(self) -> str:
        """Why this node cannot be ``COMPLETED``, in one operator-readable line."""

        if not self.success_criteria:
            return "no success criteria, so there is nothing to prove"
        unmeasured = self.criteria_without_evidence()
        failed = self.criteria_failed()
        parts = []
        if unmeasured:
            parts.append(f"{len(unmeasured)} unmeasured ({', '.join(sorted(unmeasured))})")
        if failed:
            parts.append(f"{len(failed)} failed ({', '.join(sorted(failed))})")
        return "; ".join(parts)

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        data["is_terminal"] = self.is_terminal
        data["evidence"] = [evidence.to_dict() for evidence in self.evidence]
        data["criteria_without_evidence"] = self.criteria_without_evidence()
        data["criteria_failed"] = self.criteria_failed()
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GoalNode:
        payload = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
        try:
            payload["state"] = NodeState(payload.get("state", NodeState.IDLE.value))
        except ValueError as exc:
            raise ValueError(f"Unknown goal node state: {payload.get('state')!r}") from exc
        payload["success_criteria"] = [str(criterion) for criterion in payload.get("success_criteria") or []]
        payload["constraints"] = [str(constraint) for constraint in payload.get("constraints") or []]
        payload["child_ids"] = [str(child) for child in payload.get("child_ids") or []]
        payload["evidence"] = [GoalEvidence.from_dict(item) for item in payload.get("evidence") or []]
        return cls(**payload)


class GoalTree:
    """An in-memory tree of :class:`GoalNode`, with the walk a run loop needs.

    Deliberately *not* a persistence layer: the tree lives on the
    :class:`~alpha.mission.goalloop.state.GoalState` document and is saved by the
    store that already owns atomic, checksummed writes. Building a second
    durable store here would be a fourth goal-ish persistence system, which is
    the problem this design exists to remove.

    ``clock`` is injectable so a replayed boundary reproduces the same
    ``updated_at`` instead of silently re-stamping history.
    """

    def __init__(self, *, clock: Any = time.time) -> None:
        self._nodes: dict[str, GoalNode] = {}
        self._root_id: str = ""
        self._clock = clock

    # -- reads --------------------------------------------------------------

    @property
    def root(self) -> GoalNode | None:
        return self._nodes.get(self._root_id)

    @property
    def root_id(self) -> str:
        return self._root_id

    @property
    def is_empty(self) -> bool:
        return not self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self._nodes

    @property
    def nodes(self) -> dict[str, GoalNode]:
        """A detached copy. The tree is mutated through its own methods."""
        return dict(self._nodes)

    def get(self, node_id: str) -> GoalNode | None:
        return self._nodes.get(node_id)

    def require(self, node_id: str) -> GoalNode:
        node = self._nodes.get(node_id)
        if node is None:
            raise KeyError(f"no goal node {node_id!r}")
        return node

    def children(self, node_id: str) -> list[GoalNode]:
        """Children in the walker's order: highest priority first, then creation order."""
        node = self._nodes.get(node_id)
        if node is None:
            return []
        order = {child_id: index for index, child_id in enumerate(node.child_ids)}
        found = [self._nodes[child_id] for child_id in node.child_ids if child_id in self._nodes]
        return sorted(found, key=lambda child: (-child.priority, order[child.node_id]))

    def descendants(self, node_id: str) -> list[GoalNode]:
        """Every node below *node_id*, breadth-first, excluding itself.

        Breadth-first rather than depth-first because the callers are summaries
        ("how much of this tree is done?") and a level-order list reads as one.
        """
        start = self._nodes.get(node_id)
        if start is None:
            return []
        collected: list[GoalNode] = []
        frontier = list(self.children(node_id))
        while frontier:
            node = frontier.pop(0)
            collected.append(node)
            frontier.extend(self.children(node.node_id))
        return collected

    def ancestors(self, node_id: str) -> list[GoalNode]:
        """Root-first chain, bounded.

        The bound is not defensive noise: a cycle introduced by a bad write would
        otherwise hang every reader that walks ancestry.
        """
        chain: list[GoalNode] = []
        seen: set[str] = {node_id}
        current = self._nodes.get(node_id)
        while current is not None and current.parent_id and len(chain) < MAX_ANCESTOR_DEPTH:
            if current.parent_id in seen:
                break
            seen.add(current.parent_id)
            parent = self._nodes.get(current.parent_id)
            if parent is None:
                break
            chain.append(parent)
            current = parent
        return list(reversed(chain))

    def depth(self, node_id: str) -> int:
        """Root is depth 0."""
        return len(self.ancestors(node_id))

    def priority_ceiling(self, node_id: str) -> int:
        """The lowest priority among a node and its ancestors."""
        node = self._nodes.get(node_id)
        if node is None:
            return 0
        return min([ancestor.priority for ancestor in self.ancestors(node_id)] + [node.priority])

    # -- the walk -----------------------------------------------------------

    def next_runnable_node(self) -> GoalNode | None:
        """The next node the run loop should work on, or ``None``.

        Depth-first, priority-ordered among siblings. Terminal nodes are skipped,
        a gated ancestor hides its whole subtree, and an internal node that is
        working *through* its children defers to them - falling back to itself
        only once every child has settled, which is what lets a decomposed parent
        reach ``VERIFYING`` at all.
        """
        if not self._root_id:
            return None
        return self._first_runnable(self._root_id)

    def _first_runnable(self, node_id: str) -> GoalNode | None:
        node = self._nodes.get(node_id)
        if node is None or (node.state not in RUNNABLE_NODE_STATES and node.state is not NodeState.PARTIAL) or not self._is_ungated(node):
            return None
        children = self.children(node_id)
        if children and (node.state in _CHILD_DRIVEN_STATES or node.state is NodeState.PARTIAL):
            for child in children:
                found = self._first_runnable(child.node_id)
                if found is not None:
                    return found
            if node.state is NodeState.PARTIAL:
                return None
            # Every child has settled; the parent's own turn is now.
            return node
        return node

    def _is_ungated(self, node: GoalNode) -> bool:
        """Whether nothing between *node* and the root stops it from running."""
        current: GoalNode | None = node
        seen: set[str] = set()
        while current is not None and current.parent_id and len(seen) < MAX_ANCESTOR_DEPTH:
            if current.parent_id in seen:
                return False  # a cycle gates everything under it
            seen.add(current.parent_id)
            parent = self._nodes.get(current.parent_id)
            if parent is None:
                return False
            if parent.state in _GATING_STATES:
                return False
            if parent.state is NodeState.PARTIAL and not current.can_run_despite_partial_parent:
                return False
            current = parent
        return True

    def can_decompose(self, node_id: str) -> bool:
        """Whether *node_id* still has room to break itself into children.

        Depth, fan-out and repeated childless attempts are all bounded, which is
        what stops ``DECOMPOSING -> EXECUTING -> DECOMPOSING`` from spinning.
        """
        node = self._nodes.get(node_id)
        if node is None or node.child_ids or node.is_terminal:
            return False
        if self.depth(node_id) >= MAX_TREE_DEPTH - 1:
            return False
        return node.decompose_attempts < MAX_DECOMPOSITION_ATTEMPTS

    # -- writes -------------------------------------------------------------

    def add_root(
        self,
        objective: str,
        *,
        success_criteria: Sequence[str] | None = None,
        constraints: Sequence[str] | None = None,
        priority: int = 100,
        session_id: str = "",
    ) -> GoalNode:
        """Create the root. A tree has exactly one; a second call is refused."""
        if self._root_id and self._root_id in self._nodes:
            raise ValueError(f"this tree already has a root node {self._root_id!r}")
        node = self._new_node(
            objective=objective,
            parent_id="",
            success_criteria=success_criteria,
            constraints=constraints,
            priority=priority,
            session_id=session_id,
        )
        self._root_id = node.node_id
        return node

    def add_child(
        self,
        parent_id: str,
        objective: str,
        *,
        success_criteria: Sequence[str] | None = None,
        constraints: Sequence[str] | None = None,
        priority: int | None = None,
        session_id: str = "",
        can_run_despite_partial_parent: bool = False,
    ) -> GoalNode:
        """Create a subgoal, preserving scope, inheritance and the priority ceiling.

        A decomposition cannot promote work above the node it was derived from,
        and it cannot outgrow the tree's bounds. Both are refused here rather
        than discovered later by a parent projection that discloses the damage.
        """
        parent = self.require(parent_id)
        if parent.is_terminal:
            raise IllegalNodeTransition(f"node '{parent_id}' is terminal ('{parent.state.value}'); it cannot gain children")
        if len(parent.child_ids) >= MAX_CHILDREN_PER_NODE:
            raise ValueError(f"node '{parent_id}' already has {len(parent.child_ids)} children; the fan-out bound is {MAX_CHILDREN_PER_NODE}")
        if self.depth(parent_id) + 1 > MAX_TREE_DEPTH - 1:
            raise ValueError(f"node '{parent_id}' is at depth {self.depth(parent_id)}; the tree depth bound is {MAX_TREE_DEPTH} levels")

        ceiling = self.priority_ceiling(parent_id)
        requested = ceiling if priority is None else int(priority)
        if requested > ceiling:
            raise ValueError(f"subgoal priority {requested} exceeds the ancestor ceiling {ceiling}; a decomposition may not outrank the work it derives from")

        child = self._new_node(
            objective=objective,
            parent_id=parent_id,
            success_criteria=success_criteria,
            # A child inherits its parent's guardrails; they are not optional.
            constraints=list(parent.constraints) + list(constraints or []),
            priority=requested,
            session_id=session_id or parent.session_id,
            can_run_despite_partial_parent=can_run_despite_partial_parent,
        )
        parent.child_ids.append(child.node_id)
        parent.updated_at = self._now()
        return child

    def _new_node(
        self,
        *,
        objective: str,
        parent_id: str,
        success_criteria: Sequence[str] | None,
        constraints: Sequence[str] | None,
        priority: int,
        session_id: str,
        can_run_despite_partial_parent: bool = False,
    ) -> GoalNode:
        now = self._now()
        node = GoalNode(
            node_id=new_node_id(),
            parent_id=parent_id,
            objective=str(objective),
            success_criteria=[str(criterion) for criterion in (success_criteria or [])],
            constraints=[str(constraint) for constraint in (constraints or [])],
            priority=int(priority),
            session_id=str(session_id),
            created_at=now,
            updated_at=now,
            can_run_despite_partial_parent=can_run_despite_partial_parent,
        )
        self._nodes[node.node_id] = node
        return node

    def _now(self) -> float:
        return float(self._clock())

    def bind_clock(self, clock: Any) -> None:
        """Hand the tree the run loop's clock.

        The loop injects a clock so a replayed boundary reproduces the same
        timestamps. The tree is part of that boundary, so it must read the same
        clock rather than the module default.
        """
        self._clock = clock

    def transition(self, node_id: str, target: NodeState, *, reason: str = "") -> GoalNode:
        """Move a node, refusing an illegal or terminal transition."""
        node = self.require(node_id)
        if node.state is target:
            return node
        if node.is_terminal:
            raise IllegalNodeTransition(f"node '{node_id}' is terminal ('{node.state.value}'); it cannot move to '{target.value}'")
        if target is NodeState.COMPLETED:
            unfinished = [child for child in self.children(node_id) if not child.is_terminal]
            detail = node.refusal_detail()
            if detail or unfinished:
                reasons = [detail] if detail else []
                if unfinished:
                    reasons.append(f"{len(unfinished)} unfinished child node(s)")
                raise IllegalNodeTransition(f"node '{node_id}' cannot be completed ({'; '.join(reasons)})")
        allowed = NODE_TRANSITIONS.get(node.state, frozenset())
        if target not in allowed:
            raise IllegalNodeTransition(f"node '{node_id}' cannot move from '{node.state.value}' to '{target.value}'; allowed: {sorted(state.value for state in allowed)}")
        previous = node.state
        node.state = target
        node.updated_at = self._now()
        if target is NodeState.BLOCKED:
            node.blocked_reason = reason
        elif target is NodeState.REPLANNING:
            # A replan is a new plan revision, so the link into the versioned
            # plan store moves with it.
            node.replan_count += 1
            node.plan_version += 1
            node.plan_id = ""
        elif previous is NodeState.BLOCKED:
            node.blocked_reason = ""
        return node

    def decompose(self, node_id: str, children: Iterable[Mapping[str, Any] | str]) -> list[GoalNode]:
        """Break *node_id* into children and move it to ``EXECUTING`` through them.

        Each entry is either an objective string or a mapping with ``objective``
        and optional ``success_criteria`` / ``constraints`` / ``priority``. A node
        that produces no children twice in a row is forced to ``EXECUTING`` with
        whatever it has, because a decomposer that keeps returning nothing would
        otherwise spin the loop forever.
        """
        node = self.require(node_id)
        if node.state is not NodeState.DECOMPOSING:
            raise IllegalNodeTransition(f"node '{node_id}' is '{node.state.value}', not 'decomposing'; it cannot be decomposed")

        specs = list(children)
        if specs and len(node.child_ids) + len(specs) > MAX_CHILDREN_PER_NODE:
            raise ValueError(f"decomposing '{node_id}' into {len(specs)} children would exceed the fan-out bound of {MAX_CHILDREN_PER_NODE}")

        created: list[GoalNode] = []
        for spec in specs:
            if isinstance(spec, str):
                created.append(self.add_child(node_id, spec))
                continue
            created.append(
                self.add_child(
                    node_id,
                    str(spec.get("objective", "")),
                    success_criteria=spec.get("success_criteria"),
                    constraints=spec.get("constraints"),
                    priority=spec.get("priority"),
                    can_run_despite_partial_parent=bool(spec.get("can_run_despite_partial_parent", False)),
                )
            )

        if created:
            node.decompose_attempts = 0
            # The parent is now executing *via* its children.
            self.transition(node_id, NodeState.EXECUTING)
            return_children = created
        else:
            node.decompose_attempts += 1
            return_children = []

        if node.decompose_attempts >= MAX_DECOMPOSITION_ATTEMPTS:
            # Bounded: a node that cannot describe its own children executes
            # directly rather than decomposing forever.
            self.transition(node_id, NodeState.EXECUTING, reason="decomposition produced no children")
        return return_children

    def record_evidence(self, node_id: str, evidence: GoalEvidence) -> GoalNode:
        """Attach one measurement. Unmeasured evidence is stored but decides nothing."""
        node = self.require(node_id)
        node.evidence.append(evidence)
        node.updated_at = self._now()
        return node

    def request_completion(self, node_id: str, *, reason: str = "") -> GoalNode:
        """Enter ``COMPLETED``, or raise with the real refusal.

        A node with an unmeasured or failed criterion cannot close, and a node
        with unfinished children cannot close either - a parent that declares
        itself done while a child is still running has hidden the remaining work.
        ``PARTIAL`` stays reachable for the honest partial result; it is not a
        consolation prize behind this gate.
        """
        node = self.require(node_id)
        if node.is_terminal:
            raise IllegalNodeTransition(f"node '{node_id}' is terminal ('{node.state.value}'); it cannot be completed")
        return self.transition(node_id, NodeState.COMPLETED, reason=reason)

    def verify(self, node_id: str, *, reason: str = "") -> GoalNode:
        """The verification-first flow.

        A node whose criteria are not all measured enters ``VERIFYING`` - where
        that transition is legal - so the work that would measure them can run; a
        node whose criteria are all measured closes through the completion gate,
        the only thing that may write ``COMPLETED``.
        """
        node = self.require(node_id)
        if node.is_terminal:
            raise IllegalNodeTransition(f"node '{node_id}' is terminal ('{node.state.value}'); it cannot be verified")
        if node.criteria_without_evidence() or node.criteria_failed():
            # Already in VERIFYING is a no-op transition, so a node
            # mid-verification reports missing measurements, not an error.
            return self.transition(node_id, NodeState.VERIFYING, reason=reason or node.refusal_detail())
        return self.request_completion(node_id, reason=reason or "all criteria measured")

    def propagate_completion(self, node_id: str) -> list[str]:
        """Close out *node_id*'s ancestors once every one of their children has settled.

        Returns the node ids that moved. A parent whose criteria were never
        measured parks in ``VERIFYING`` rather than being declared failed: the
        honest answer to "is this done?" when nobody checked is "unverified", and
        ``VERIFYING`` is the state that lets the work which would check it run.
        """
        moved: list[str] = []
        current = self._nodes.get(node_id)
        if current is None:
            return moved
        parent = self._nodes.get(current.parent_id) if current.parent_id else None
        while parent is not None and not parent.is_terminal:
            children = self.children(parent.node_id)
            if any(child.state not in TERMINAL_NODE_STATES for child in children):
                break  # the parent still has running children; nothing to decide
            target = self._parent_verdict(parent, children)
            if target is None:
                break
            self.transition(parent.node_id, target, reason=f"all {len(children)} children settled")
            moved.append(parent.node_id)
            parent = self._nodes.get(parent.parent_id) if parent.parent_id else None
        return moved

    @staticmethod
    def _parent_verdict(parent: GoalNode, children: Sequence[GoalNode]) -> NodeState | None:
        """What a settled parent should become, from its children and its own evidence."""
        if any(child.state in {NodeState.FAILED, NodeState.CANCELLED} for child in children):
            # A child that could not be done means the parent's promise rested on
            # work that did not land. Reporting anything else would be a claim the
            # evidence does not support.
            return NodeState.FAILED

        all_completed = all(child.state is NodeState.COMPLETED for child in children)
        if not parent.success_criteria:
            # Nothing was promised at this level, so the children's own outcomes
            # *are* the parent's outcome.
            return NodeState.COMPLETED if all_completed else NodeState.PARTIAL

        if parent.criteria_without_evidence():
            return NodeState.VERIFYING
        if parent.criteria_failed():
            return NodeState.PARTIAL if parent.criteria_passed() else NodeState.FAILED
        return NodeState.COMPLETED if all_completed else NodeState.PARTIAL

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self._root_id,
            "nodes": [node.to_dict() for node in self._nodes.values()],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, clock: Any = time.time) -> GoalTree:
        tree = cls(clock=clock)
        for item in data.get("nodes") or []:
            node = GoalNode.from_dict(item)
            if not node.node_id:
                raise ValueError("goal tree node id must not be empty")
            if node.node_id in tree._nodes:
                raise ValueError(f"duplicate goal tree node id {node.node_id!r}")
            tree._nodes[node.node_id] = node
        root_id = str(data.get("root_id", ""))
        if not tree._nodes:
            if root_id:
                raise ValueError(f"empty goal tree has unknown root id {root_id!r}")
            return tree
        if root_id not in tree._nodes:
            raise ValueError(f"goal tree root id {root_id!r} does not identify a node")
        tree._root_id = root_id
        tree._validate_restored_structure()
        return tree

    def _validate_restored_structure(self) -> None:
        """Reject malformed persisted graphs before any tree walk can use them."""
        root = self._nodes[self._root_id]
        if root.parent_id:
            raise ValueError(f"goal tree root {self._root_id!r} must not have a parent")

        for node in self._nodes.values():
            if len(node.child_ids) > MAX_CHILDREN_PER_NODE:
                raise ValueError(f"goal tree node {node.node_id!r} exceeds the fan-out bound of {MAX_CHILDREN_PER_NODE}")
            if len(node.child_ids) != len(set(node.child_ids)):
                raise ValueError(f"goal tree node {node.node_id!r} has duplicate child links")
            if node is not root and not node.parent_id:
                raise ValueError(f"non-root goal tree node {node.node_id!r} has no parent")
            if node.parent_id and node.parent_id not in self._nodes:
                raise ValueError(f"goal tree node {node.node_id!r} references missing parent {node.parent_id!r}")

            for child_id in node.child_ids:
                child = self._nodes.get(child_id)
                if child is None:
                    raise ValueError(f"goal tree node {node.node_id!r} references missing child {child_id!r}")
                if child.parent_id != node.node_id:
                    raise ValueError(f"goal tree parent-child mismatch: {node.node_id!r} lists {child_id!r}, whose parent is {child.parent_id!r}")

        for node in self._nodes.values():
            if node.parent_id and node.node_id not in self._nodes[node.parent_id].child_ids:
                raise ValueError(f"goal tree parent-child mismatch: {node.node_id!r} names parent {node.parent_id!r}, which does not list it as a child")

        # Iterative traversal both enforces the construction depth bound and
        # makes cycles/shared children fail as data errors instead of recursive
        # traversal failures later in next_runnable_node()/tree_view().
        visited: set[str] = set()
        pending = [(self._root_id, 0)]
        while pending:
            node_id, depth = pending.pop()
            if node_id in visited:
                raise ValueError(f"goal tree contains a cycle or repeated child link at {node_id!r}")
            visited.add(node_id)
            if depth >= MAX_TREE_DEPTH:
                raise ValueError(f"goal tree exceeds the depth bound of {MAX_TREE_DEPTH} levels")
            node = self._nodes[node_id]
            pending.extend((child_id, depth + 1) for child_id in node.child_ids)

        if visited != self._nodes.keys():
            unreachable = sorted(self._nodes.keys() - visited)
            raise ValueError(f"goal tree contains nodes disconnected from root {self._root_id!r}: {unreachable}")

    def tree_view(self) -> dict[str, Any]:
        """One nested view of the whole tree, for status output and the UI."""

        def _node(node: GoalNode, depth: int) -> dict[str, Any]:
            return {
                "node_id": node.node_id,
                "objective": node.objective,
                "state": node.state.value,
                "priority": node.priority,
                "depth": depth,
                "plan_version": node.plan_version,
                "criteria_total": len(node.success_criteria),
                "criteria_unmeasured": len(node.criteria_without_evidence()),
                "criteria_failed": len(node.criteria_failed()),
                "children": [_node(child, depth + 1) for child in self.children(node.node_id)],
            }

        root = self.root
        return _node(root, 0) if root is not None else {}

    def progress(self) -> dict[str, Any]:
        """How much of the tree is settled, in counts."""
        nodes = list(self._nodes.values())
        return {
            "total": len(nodes),
            "completed": sum(1 for node in nodes if node.state is NodeState.COMPLETED),
            "partial": sum(1 for node in nodes if node.state is NodeState.PARTIAL),
            "failed": sum(1 for node in nodes if node.state is NodeState.FAILED),
            "cancelled": sum(1 for node in nodes if node.state is NodeState.CANCELLED),
            "unmeasured_criteria": sum(len(node.criteria_without_evidence()) for node in nodes),
        }
