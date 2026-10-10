"""RRSI component vocabulary ``K``, its structural subset, and the surface→component map.

In RRSI a harness change is attributed to exactly one *component* of ``K`` so
that credit assignment, structured exploration and L1 structural pruning all
have something to count. The paper's workspace vocabulary is nine components;
this module adopts it verbatim and then maps Alpha's own evolvable surfaces
onto it.

Binding rules:

* **The vocabulary is declared, not inferred.** ``COMPONENTS`` is a literal.
  Nothing here inspects a diff to decide what a component is — a classifier
  that guessed would make every downstream statistic depend on a guess.
* **The surface→component map is a disclosed heuristic, and its domain is
  pinned to the real allow-set.** :data:`SURFACE_COMPONENTS` must cover
  exactly :data:`alpha.rsi.generator.EVOLVABLE_SURFACES`; that equality is
  asserted by ``tests/test_rrsi_components.py`` rather than re-declared here,
  because importing the generator from this module would invert the import
  direction (``alpha.rsi.generator`` imports the proposal plan built on top of
  this file). Every produced tag therefore carries its ``basis`` so a caller
  can see *why* a component was chosen instead of having to trust it.
* **Nothing in this module is a score.** :func:`novelty` counts untried
  structural components; it does not estimate diversity or quality.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

__all__ = [
    "COMPONENTS",
    "STRUCTURAL_COMPONENTS",
    "SURFACE_COMPONENTS",
    "ComponentTag",
    "component_for",
    "components_for",
    "novelty",
    "novelty_breakdown",
    "validate_component",
]

#: ``K`` — the nine harness components RRSI attributes changes to.
COMPONENTS: tuple[str, ...] = (
    "prompt",
    "control_flow",
    "config",
    "output_plumbing",
    "context_mgmt",
    "client_tool",
    "skill",
    "memory",
    "subagent",
)

#: ``K_struct`` ⊂ ``K`` — the components whose practice the novelty utility
#: credits. :data:`STRUCTURAL_COMPONENTS` is never derived at runtime: it is
#: the declared set the utility is defined over, so its value cannot drift
#: between two call sites.
STRUCTURAL_COMPONENTS: frozenset[str] = frozenset({"client_tool", "skill", "memory", "subagent"})

#: Alpha's five evolvable surfaces → the component they predominantly touch.
#: This table's key set is pinned by test to
#: ``alpha.rsi.generator.EVOLVABLE_SURFACES`` (see the module docstring).
SURFACE_COMPONENTS: dict[str, str] = {
    "prompt": "prompt",
    "skill": "skill",
    "routing": "control_flow",
    "memory_retrieval": "memory",
    "code": "control_flow",
}

#: Target-string hints, most specific first: the first case-insensitive
#: substring hit wins. Hints exist because Alpha's ``target`` strings are far
#: more specific than its five surfaces (``compaction`` and ``tool_router``
#: are both ``target_component`` values on the *same* RSI surface), and
#: attributing both of them to one component would make the credit assignment
#: useless for deciding which component to prune.
_TARGET_HINTS: tuple[tuple[str, str], ...] = (
    ("context_pruner", "context_mgmt"),
    ("compaction", "context_mgmt"),
    ("keep_last_observations", "context_mgmt"),
    ("strip_threshold", "context_mgmt"),
    ("prune", "context_mgmt"),
    ("compact", "context_mgmt"),
    ("tool_router", "control_flow"),
    ("stream", "output_plumbing"),
    ("output_plumbing", "output_plumbing"),
    ("pruning", "context_mgmt"),
    ("router", "control_flow"),
    ("routing", "control_flow"),
    ("retriev", "memory"),
    ("memory", "memory"),
    ("subagent", "subagent"),
    ("delegat", "subagent"),
    ("skill", "skill"),
    ("prompt", "prompt"),
    ("soul", "prompt"),
    ("config", "config"),
    ("budget", "config"),
    ("timeout", "control_flow"),
    ("tool", "client_tool"),
)


@dataclass(frozen=True)
class ComponentTag:
    """One component attribution plus *why* it was chosen.

    ``basis`` is one of ``"target_hint"`` (a :data:`_TARGET_HINTS` entry
    matched ``target``), ``"surface"`` (the surface's declared default) or
    ``"surface_default"`` (no target supplied). ``matched_hint`` is ``None``
    unless ``basis == "target_hint"``. Carrying the basis is what lets a
    disclosure say "attributed to ``context_mgmt`` because target
    ``compaction`` matched hint ``compaction``" rather than asserting the
    component as fact.
    """

    component: str
    basis: str
    matched_hint: str | None = None
    surface: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"component": self.component, "basis": self.basis, "matched_hint": self.matched_hint, "surface": self.surface}


def validate_component(component: str) -> str:
    """Return ``component`` if it is in :data:`COMPONENTS`, else raise."""
    if not isinstance(component, str) or component not in COMPONENTS:
        raise ValueError(f"unknown RRSI component {component!r}; K = {list(COMPONENTS)}.")
    return component


def component_for(surface: str, *, target: str = "") -> ComponentTag:
    """Attribute a change to exactly one component of ``K``.

    ``surface`` must be a key of :data:`SURFACE_COMPONENTS`; an unknown surface
    raises rather than falling back to a default, because a silent fallback
    would file every typo under one component and quietly dominate its credit
    statistics.
    """
    base = SURFACE_COMPONENTS.get(surface)
    if base is None:
        raise ValueError(f"surface {surface!r} has no RRSI component mapping; mapped surfaces are {sorted(SURFACE_COMPONENTS)}.")
    if isinstance(target, str) and target:
        lowered = target.lower()
        for hint, component in _TARGET_HINTS:
            if hint in lowered:
                return ComponentTag(component=component, basis="target_hint", matched_hint=hint, surface=surface)
        return ComponentTag(component=base, basis="surface", matched_hint=None, surface=surface)
    return ComponentTag(component=base, basis="surface_default", matched_hint=None, surface=surface)


def components_for(surface: str, *, targets: Iterable[str]) -> tuple[ComponentTag, ...]:
    """Attribute several targets on one surface, de-duplicated by component.

    Order is first-occurrence order of the *component*, so the disclosure is
    stable for a fixed target list.
    """
    seen: dict[str, ComponentTag] = {}
    for target in targets:
        tag = component_for(surface, target=target)
        seen.setdefault(tag.component, tag)
    return tuple(seen.values())


def novelty(comp: Collection[str] | None, winning: Collection[str] | None) -> int | None:
    """``ν_t(H')`` — structural component types ``H'`` touches that have never won.

    ``ν_t(H') = Σ_{ℓ ∈ K_str} 1[ ℓ ∈ comp(H') ∧ N_t(ℓ) = 0 ]`` — the count is
    over the *candidate's own* components, not over ``K_str`` in general: a
    candidate that touches nothing structural earns no credit however many
    structural components remain untried elsewhere.

    ``comp`` is ``comp(H')`` (this candidate's components); ``winning`` is the
    set of components that have appeared in a winning edit (``N_t(ℓ) = 1``).
    Returns ``None`` when either is ``None``, which callers obtain only when
    the credit-assignment ledger could not be read — reporting ``0`` there
    would claim the candidate earned no novelty credit rather than that the
    credit could not be computed.
    """
    if comp is None or winning is None:
        return None
    structural_touched = STRUCTURAL_COMPONENTS & frozenset(comp)
    return len(structural_touched - frozenset(winning))


def novelty_breakdown(comp: Collection[str] | None, winning: Collection[str] | None) -> dict[str, object]:
    """The novelty count *and* the two sets it was computed from.

    Returned together because a bare ``ν = 1`` is not checkable: without both
    memberships a reader cannot tell which component the credit was granted
    for, or whether it has in fact won before.
    """
    value = novelty(comp, winning)
    if value is None:
        return {"novelty": None, "structural_touched": None, "never_won": None, "reason": "credit-assignment ledger unavailable — novelty not computed"}
    structural_touched = sorted(STRUCTURAL_COMPONENTS & frozenset(comp))
    never_won = sorted((STRUCTURAL_COMPONENTS & frozenset(comp)) - frozenset(winning))
    return {"novelty": value, "structural_touched": structural_touched, "never_won": never_won, "reason": ""}
