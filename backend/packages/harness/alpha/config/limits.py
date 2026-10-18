"""The single authority for depth and fan-out ceilings.

Every subsystem that bounds *how deep* or *how wide* a delegation tree,
workflow wave or group run may go reads its number from here. Before this
module existed the same concepts were re-declared in half a dozen files and
disagreed: subagent depth was ``3`` in three places and ``5`` in a fourth,
children-per-parent was ``10`` twice and ``4`` once, and the workflow wave
concurrency ceiling was ``32`` twice. A reader who wanted to know "how deep
may this go" had no answer that was true everywhere.

Three rules hold for this module:

1. **One number, one value.** A consumer imports a name; it never re-states
   the literal. Two modules that must agree therefore *cannot* disagree,
   because there is only one place to change.
2. **The values are unchanged.** This is a pure centralisation: every constant
   below is the exact value the consuming module enforced before it was moved
   here, so no behaviour changes with it. Changing a ceiling is a separate,
   deliberate decision — and a reviewable one, because it now lands in one
   file.
3. **Policy is not mechanics.** A *profile* bound — an APEX autonomy contract's
   ``max_delegation_depth``, a bot model-config's fallback-count bound — is a
   policy value intersected against these engine ceilings at read time, never
   merged into them. ``alpha.apex.agents.resolve_delegation_limits`` is where
   that intersection happens for APEX, and it names both numbers in its
   refusal. Merging the two would turn a tunable policy knob into a hard
   engine refusal (or worse, the reverse).

This module is **stdlib-free on purpose**: it is imported at module scope by
config models, the subagent lifecycle manager, workflow execution and the
groups scope, so anything heavier here would risk an import cycle at exactly
the layer that must never have one.

Drift is pinned by ``tests/test_limit_authority_consistency.py``, which asserts
every consumer site's value equals the constant imported here.
"""

from __future__ import annotations

__all__ = [
    "MAX_ATTEMPTS_PER_TASK",
    "MAX_CHILDREN_PER_PARENT",
    "MAX_CONCURRENT_NATIVE_SUBAGENTS_HARD",
    "MAX_DELEGATION_DEPTH",
    "MAX_DELEGATION_HOPS",
    "MAX_FANOUT_PER_NODE",
    "MAX_GROUP_NESTING_DEPTH",
    "MAX_GROUP_PARALLEL_MEMBERS",
    "MAX_GROUP_RELAY_HOPS",
    "MAX_REVISITS_PER_TASK",
    "MAX_SWARM_PLAN_CONCURRENCY",
    "MAX_WALL_CLOCK_SECONDS",
    "MAX_WAVE_CONCURRENCY_CEILING",
    "MAX_TOTAL_TASKS",
    "DEFAULT_CONCURRENT_NATIVE_SUBAGENTS",
    "DEFAULT_WAVE_CONCURRENCY",
]

# --------------------------------------------------------------------------
# Delegation axis — subagent trees, bot delegation, autonomy bounds
# --------------------------------------------------------------------------

#: How many levels of ``task`` recursion one delegation tree may nest. Three
#: is a cost decision, not a capability one: each level pays a fresh context
#: window, and a three-deep tree already isolates three layers of work.
MAX_DELEGATION_DEPTH = 3

#: How many *simultaneously active* children one parent node may hold. Ten
#: gives a planner room to fan out a real decomposition without letting one
#: node monopolise the process execution slots.
MAX_CHILDREN_PER_PARENT = 10

#: How many children a single delegation node may *spawn in total* (not
#: concurrently) — the sibling budget, distinct from ``MAX_CHILDREN_PER_PARENT``
#: which bounds the concurrent live ones. Four is deliberately narrower: a node
#: that wants more siblings than that is decomposing work it should itself be
#: planning.
MAX_FANOUT_PER_NODE = 4

#: How many sequential delegation hops (parent → child → grandchild → ...)
#: a tree may contain, counting each level's handoff. Wider than the depth
#: ceiling because a hop also counts lateral transfers that are not nesting.
MAX_DELEGATION_HOPS = 6

# --------------------------------------------------------------------------
# Native subagent process admission
# --------------------------------------------------------------------------

#: How many native subagents the process-wide async FIFO admission controller
#: allows to *run* concurrently by default. This is the ``subagent_runtime``
#: schema default, and the value ``SubagentExecutor``'s module-level
#: ``MAX_CONCURRENT_SUBAGENTS`` alias resolves to.
DEFAULT_CONCURRENT_NATIVE_SUBAGENTS = 3

#: Schema ceiling on ``subagent_runtime.max_running`` — a deployment may raise
#: the running count, but never past this.
MAX_CONCURRENT_NATIVE_SUBAGENTS_HARD = 1000

# --------------------------------------------------------------------------
# Workflow wave axis — dynamic workflow graph execution
# --------------------------------------------------------------------------

#: How many nodes within one wave the wave executor runs on a bounded pool
#: when ``policies.max_concurrency`` is declared. Four is a thread-pool
#: budget: an undeclared workflow still runs sequentially, and a declared one
#: gets real overlap on disjoint ``write_scope``s.
DEFAULT_WAVE_CONCURRENCY = 4

#: Hard ceiling on a declared wave concurrency. A typo'd ``max_concurrency``
#: must not turn a graph into a thread bomb against the Gateway process. Both
#: the engine's own ``MAX_CONCURRENCY_CEILING`` and the swarm node's
#: ``MAX_SWARM_CONCURRENCY`` resolve to this same number — a workflow node and
#: a swarm member group are the same fan-out mechanism wearing different names.
MAX_WAVE_CONCURRENCY_CEILING = 32

# --------------------------------------------------------------------------
# Group axis — nested groups, relay fan-out, parallel members
# --------------------------------------------------------------------------

#: Maximum nesting depth for a group tree. Four is deliberate: deeper makes
#: the rendered ``path`` unreadable in a sidebar, and relay fan-out becomes a
#: cost problem rather than a feature. A room created below this is refused,
#: never clamped. This is *group* nesting, a separate domain from delegation
#: depth: a group is an organizational container, not an execution recursion,
#: so the two numbers legitimately differ.
MAX_GROUP_NESTING_DEPTH = 4

#: Maximum relay hops a single relayed message may travel before the relay
#: loop guard stops it. The default hop allowance (``DEFAULT_MAX_HOP = 3`` in
#: ``groups.scope``) is a per-run policy value; this is the ceiling it is
#: clamped against.
MAX_GROUP_RELAY_HOPS = 8

#: How many group members the group runner executes concurrently. Three is a
#: deliberation-size decision: a room wider than three parallel speakers
#: produces a transcript nobody reads back, and the router's own
#: ``max_parallel`` request field is validated against this same ceiling.
MAX_GROUP_PARALLEL_MEMBERS = 3

# --------------------------------------------------------------------------
# Swarm plan axis — the ``alpha.swarm`` plan engine (distinct from the
# workflow ``SWARM`` node, which shares the wave ceiling above)
# --------------------------------------------------------------------------

#: Ceiling on a swarm *plan*'s task concurrency: how many tasks
#: ``AsyncSwarmRunner`` runs in parallel for one plan. The Gateway router's
#: ``max_concurrency`` request field refuses above this, and
#: ``SwarmCoordinator.create_swarm`` clamps internal callers to the same
#: number — two sites that must agree, or a request the router accepts gets
#: silently clamped downstream instead of being refused with the bound named.
#: This is a *plan* worker budget, a separate domain from the workflow
#: ``SWARM`` node's member fan-out (``MAX_WAVE_CONCURRENCY_CEILING``), so the
#: two numbers legitimately differ.
MAX_SWARM_PLAN_CONCURRENCY = 1000

#: Maximum explicit work items in one swarm request. A map/reduce plan adds a
#: single gather node on top, so its task budget must allow items + 1.
MAX_SWARM_PLAN_ITEMS = 1000

#: Maximum inbound dependencies (fan-in) on a single swarm task node. The
#: map/reduce gather node depends on every map task it combines, so this
#: ceiling must follow the item ceiling rather than being an independent
#: number that can silently refuse a legal plan size.
MAX_SWARM_TASK_DEPENDENCIES = MAX_SWARM_PLAN_ITEMS

# --------------------------------------------------------------------------
# Delegation attempt / wall-clock / total-task / revisit axis
# --------------------------------------------------------------------------
#
# These four were literals inside ``alpha.bots.autonomy_guard`` (and the
# attempt ceiling was a second literal in ``alpha.subagents.lifecycle``) —
# the same concepts as the depth/fan-out axis above, but with a private copy
# of each number. They live here now so "one number, one value" holds for
# the whole axis; the values are exactly what those modules enforced before
#: the move, so no behaviour changes with it.

#: How many times one task may be dispatched before it is refused. Both
#: ``AutonomyBounds.max_attempts_per_task`` and the subagent lifecycle
#: manager's ``DEFAULT_MAX_ATTEMPTS`` resolve to this.
MAX_ATTEMPTS_PER_TASK = 3

#: Wall-clock ceiling for one unattended delegation tree, in seconds.
MAX_WALL_CLOCK_SECONDS = 1800.0

#: Ceiling on the total number of tasks one unattended tree may dispatch.
MAX_TOTAL_TASKS = 200

#: How many times one task may be re-dispatched (the revisit half of the
#: cycle guard) before the tree is stopped.
MAX_REVISITS_PER_TASK = 2
