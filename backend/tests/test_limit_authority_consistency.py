"""One authority for depth/fan-out ceilings — and nothing re-states the number.

Before ``alpha.config.limits`` existed the same concepts were re-declared in
half a dozen files and disagreed: subagent depth was ``3`` in three places,
children-per-parent was ``10`` twice and ``4`` once, and the workflow wave
concurrency ceiling was ``32`` twice. These tests pin the fix in both
directions:

1. every consumer site's value **equals** the constant imported from the
   authority, so two modules that must agree cannot disagree; and
2. the authority module stays **stdlib-free**, because it is imported at module
   scope by config models, the subagent lifecycle manager, workflow execution
   and the groups scope — anything heavier there would risk an import cycle at
   exactly the layer that must never have one.

The values themselves are unchanged from what each consumer enforced before
the move, so this is a pure centralisation: changing a ceiling is a separate,
deliberate, reviewable edit in one file.

APEX is deliberately **not** merged into the authority: an autonomy contract's
``max_delegation_depth`` is a *policy* value intersected against the engine
ceiling at read time (``resolve_delegation_limits``), so a test here pins that
the engine ceiling is the binding constraint rather than re-typing its number.
"""

from __future__ import annotations

from pathlib import Path

from alpha.config import limits


def _field_ceiling(field) -> int:
    """Find the ``le`` (upper-bound) constraint on a pydantic v2 field.

    ``field.metadata`` is a flat list of constraint objects (``Ge``, ``Le``,
    …) in declaration order, so indexing ``[0]`` grabs whichever bound was
    written first — here ``ge=1``. Scan for the constraint that actually
    carries ``le`` instead of assuming an index.
    """
    for constraint in field.metadata:
        if hasattr(constraint, "le"):
            return int(constraint.le)
    raise AssertionError(f"field {field!r} declares no upper bound (le) — did the schema drop its ceiling?")


# --------------------------------------------------------------------------- #
# The authority's values are pinned here, so changing one is a deliberate edit
# --------------------------------------------------------------------------- #


def test_the_authority_values_are_pinned():
    assert limits.MAX_DELEGATION_DEPTH == 3
    assert limits.MAX_CHILDREN_PER_PARENT == 10
    assert limits.MAX_FANOUT_PER_NODE == 4
    assert limits.MAX_DELEGATION_HOPS == 6
    assert limits.DEFAULT_CONCURRENT_NATIVE_SUBAGENTS == 3
    assert limits.MAX_CONCURRENT_NATIVE_SUBAGENTS_HARD == 300
    assert limits.DEFAULT_WAVE_CONCURRENCY == 4
    assert limits.MAX_WAVE_CONCURRENCY_CEILING == 32
    assert limits.MAX_GROUP_NESTING_DEPTH == 4
    assert limits.MAX_GROUP_RELAY_HOPS == 8
    assert limits.MAX_GROUP_PARALLEL_MEMBERS == 3


def test_the_authority_module_is_stdlib_free():
    """Imported at module scope by layers that must never cycle.

    ``alpha.config.limits`` may not import anything heavier than ``__future__``
    and ``typing``; a stray ``from alpha...`` here is the cycle risk the module
    exists to avoid.
    """
    source = Path(limits.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        assert not stripped.startswith(("import alpha", "from alpha")), f"limits.py must stay stdlib-free, found: {stripped!r}"


# --------------------------------------------------------------------------- #
# Delegation axis: lifecycle manager, autonomy bounds, delegation limits
# --------------------------------------------------------------------------- #


def test_subagent_lifecycle_reads_the_authority():
    from alpha.subagents.lifecycle import SubagentLifecycleManager

    assert SubagentLifecycleManager.DEFAULT_MAX_DEPTH == limits.MAX_DELEGATION_DEPTH
    assert SubagentLifecycleManager.DEFAULT_MAX_CHILDREN_PER_PARENT == limits.MAX_CHILDREN_PER_PARENT


def test_autonomy_bounds_read_the_authority():
    from alpha.bots.autonomy_guard import AutonomyBounds

    bounds = AutonomyBounds()
    assert bounds.max_depth == limits.MAX_DELEGATION_DEPTH
    assert bounds.max_children_per_parent == limits.MAX_CHILDREN_PER_PARENT


def test_delegation_limits_read_the_authority():
    from alpha.bots.delegation import DelegationLimits

    delegation = DelegationLimits()
    assert delegation.max_depth == limits.MAX_DELEGATION_DEPTH
    assert delegation.max_fanout == limits.MAX_FANOUT_PER_NODE
    assert delegation.max_hops == limits.MAX_DELEGATION_HOPS
    assert delegation.max_children_per_node == limits.MAX_FANOUT_PER_NODE


# --------------------------------------------------------------------------- #
# Native subagent process admission
# --------------------------------------------------------------------------- #


def test_executor_concurrency_reads_the_authority():
    from alpha.subagents.executor import MAX_CONCURRENT_SUBAGENTS

    assert MAX_CONCURRENT_SUBAGENTS == limits.DEFAULT_CONCURRENT_NATIVE_SUBAGENTS


def test_runtime_schema_reads_the_authority():
    from alpha.config.subagent_runtime_config import SubagentRuntimeConfig

    config = SubagentRuntimeConfig()
    assert config.max_running == limits.DEFAULT_CONCURRENT_NATIVE_SUBAGENTS
    # The schema ceiling is the authority's hard ceiling, not a re-typed 64.
    field = SubagentRuntimeConfig.model_fields["max_running"]
    assert _field_ceiling(field) == limits.MAX_CONCURRENT_NATIVE_SUBAGENTS_HARD


# --------------------------------------------------------------------------- #
# Workflow wave axis: execution pool and the SWARM node share one fan-out
# --------------------------------------------------------------------------- #


def test_wave_execution_reads_the_authority():
    from alpha.workflow.execution import DEFAULT_MAX_CONCURRENCY, MAX_CONCURRENCY_CEILING

    assert DEFAULT_MAX_CONCURRENCY == limits.DEFAULT_WAVE_CONCURRENCY
    assert MAX_CONCURRENCY_CEILING == limits.MAX_WAVE_CONCURRENCY_CEILING


def test_swarm_execution_shares_the_wave_axis():
    """A workflow node and a swarm member group are one fan-out mechanism."""
    from alpha.workflow.swarm.execution import MAX_SWARM_CONCURRENCY, SwarmConfig

    assert MAX_SWARM_CONCURRENCY == limits.MAX_WAVE_CONCURRENCY_CEILING
    assert SwarmConfig().max_concurrency == limits.DEFAULT_WAVE_CONCURRENCY


# --------------------------------------------------------------------------- #
# Group axis: nesting depth, relay hops, parallel members
# --------------------------------------------------------------------------- #


def test_group_scope_reads_the_authority():
    from alpha.groups.scope import DEFAULT_MAX_HOP, MAX_DEPTH, MAX_HOP

    assert MAX_DEPTH == limits.MAX_GROUP_NESTING_DEPTH
    assert MAX_HOP == limits.MAX_GROUP_RELAY_HOPS
    # The per-run default is policy; it stays below the ceiling it is clamped to.
    assert DEFAULT_MAX_HOP <= limits.MAX_GROUP_RELAY_HOPS


def test_group_runner_reads_the_authority():
    from alpha.groups.runner import _MAX_PARALLEL_MEMBERS

    assert _MAX_PARALLEL_MEMBERS == limits.MAX_GROUP_PARALLEL_MEMBERS


def test_group_router_bound_matches_the_runner():
    """The request field's ``le=`` is the same number the runner enforces."""
    from app.gateway.routers.groups import GroupRunRequest

    field = GroupRunRequest.model_fields["max_parallel"]
    assert field.default == limits.MAX_GROUP_PARALLEL_MEMBERS
    assert _field_ceiling(field) == limits.MAX_GROUP_PARALLEL_MEMBERS
    # A request naming more than the ceiling is refused at validation, not clamped.
    import pytest

    with pytest.raises(ValueError):
        GroupRunRequest(max_parallel=limits.MAX_GROUP_PARALLEL_MEMBERS + 1)


# --------------------------------------------------------------------------- #
# Swarm plan axis — the router's refusal and the coordinator's clamp agree
# --------------------------------------------------------------------------- #


def test_swarm_plan_ceiling_is_one_number_at_both_sites():
    """A request the router accepts must not be silently clamped downstream.

    ``SwarmCreateRequest.max_concurrency`` refuses above the ceiling at the
    edge, and ``SwarmCoordinator.create_swarm`` clamps internal callers to the
    same number. If the two drift, a request the router *accepts* gets clamped
    to a smaller value later — the silent-clamp-instead-of-refusal failure the
    authority exists to prevent. The clamp is an inline ``min(...)`` rather
    than a named constant, so the pin is the router's real ``le`` (behavioural)
    plus the coordinator's own source referencing the authority (not a
    re-typed ``64``).
    """
    from app.gateway.routers.swarms import SwarmCreateRequest

    field = SwarmCreateRequest.model_fields["max_concurrency"]
    assert _field_ceiling(field) == limits.MAX_SWARM_PLAN_CONCURRENCY
    # A request naming more than the ceiling is refused at validation.
    import pytest

    with pytest.raises(ValueError):
        SwarmCreateRequest(goal="g" * 4, max_concurrency=limits.MAX_SWARM_PLAN_CONCURRENCY + 1)

    import alpha.swarm.coordinator as coordinator_module

    source = Path(coordinator_module.__file__).read_text(encoding="utf-8")
    assert "limits.MAX_SWARM_PLAN_CONCURRENCY" in source, "the coordinator clamp must read the authority, not a literal"
    assert "min(max(1, int(max_concurrency)), 64)" not in source, "a re-typed 64 in the coordinator is exactly the drift this test exists to catch"


# --------------------------------------------------------------------------- #
# APEX intersects policy with the engine ceiling — it does not re-state it
# --------------------------------------------------------------------------- #


def test_apex_contract_depth_is_intersected_with_the_authority():
    """A policy depth of 5 must not become a way out of the engine's limit.

    ``resolve_delegation_limits`` is where the intersection happens, and the
    returned depth is ``min(contract, authority)`` — so raising an APEX
    profile's ceiling cannot exceed what the subagent manager enforces.
    """
    from alpha.apex.agents import resolve_delegation_limits
    from alpha.apex.contract import AutonomyProfile, profile_for

    apex_max = profile_for(AutonomyProfile.APEX_MAX)
    # The APEX_MAX profile's own policy depth is 5...
    assert apex_max.budget.max_delegation_depth == 5
    # ...but the effective ceiling is the engine's, decided in one place.
    max_depth, _max_children, _max_total = resolve_delegation_limits(apex_max)
    assert max_depth == limits.MAX_DELEGATION_DEPTH


def test_a_narrower_policy_still_wins_over_the_authority():
    """Policy may narrow below the engine ceiling; it may never widen past it."""
    from alpha.apex.agents import resolve_delegation_limits
    from alpha.apex.contract import AutonomyProfile, narrow_contract, profile_for

    narrowed = narrow_contract(profile_for(AutonomyProfile.APEX_MAX), budget={"max_delegation_depth": 2})
    max_depth, _children, _total = resolve_delegation_limits(narrowed)
    assert max_depth == 2
