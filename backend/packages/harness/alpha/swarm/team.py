"""Specialist team composition for the Alpha swarm runtime.

A *team* is not a new lifecycle, a new plan, or a new run record. It is a
:class:`~alpha.swarm.models.SwarmPlan` whose task nodes carry a declared
capability requirement and an assignment to a named specialist. Everything else
-- the DAG, the leases, the retry policy, the budget, the event journal, the
checkpoint, the aggregator, the terminal states -- is the existing swarm
runtime, reached through the existing entry points. This module adds
composition and reporting; it deliberately adds no state of its own.

The four properties this module is responsible for
--------------------------------------------------
1. **A specialist is a declared capability, not a label.** A
   :class:`SpecialistProfile` offers capability tags through the existing
   :mod:`alpha.capabilities.eligibility` vocabulary, and a task declares what
   it requires. Selection is a *hard filter* through the existing
   :func:`~alpha.capabilities.eligibility.eligible_candidates`, not a score, so
   a specialist that does not declare the capability is unreachable however
   senior it looks.
2. **A task nobody can take stays unassigned and says why.** There is no
   "nearest match" fallback. An unassignable task keeps the swarm's existing
   default worker path and is reported in ``unassigned`` with the missing tags
   and the full rejection list, so the plan never claims coverage it lacks.
3. **Execution and acceptance stay separate.** This module never writes
   ``state``, ``acceptance_status`` or ``verification``. Those belong to
   :class:`~alpha.swarm.scheduler.SwarmScheduler` and
   :class:`~alpha.swarm.aggregator.SwarmAggregator`, and a specialist that ran
   but whose acceptance criteria failed is reported as exactly that.
4. **The strategy record is never second-guessed here.** This module does not
   import :mod:`alpha.swarm.strategy` and does not write ``plan.mode`` or
   ``plan.metrics["strategy"]``. The rebuild-until-agree loop in
   :class:`~alpha.swarm.decomposer.SwarmTaskDecomposer` is the only writer of
   that invariant, and composition runs strictly afterwards.

The roster seam
---------------
The declarative ``specialists:`` configuration section is owned by the config
layer, so this module does **not** read it. It defines the interface instead:

    register_roster_provider(provider, source="...")

where ``provider`` is any zero-argument callable returning a roster. The
catalogue owner calls that once at load time; until it does,
:func:`resolve_roster` returns an empty roster together with an explicit reason,
and the swarm behaves exactly as it did before this module existed. That
fail-open is deliberate: a missing catalogue must degrade a plan, never break
plan construction.

The two rules a registered roster must satisfy are both enforced here rather
than trusted: a second provider registered from a different source is refused
(:class:`RosterConflictError`) so two configuration owners cannot silently
fight over who the team is, and a roster entry with no name or with duplicate
names is dropped with a recorded reason rather than silently deduplicated.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.capabilities.eligibility import (
    ASSIGNABLE_BOT_STATUSES,
    MAX_REQUIRED_TAGS,
    eligible_candidates,
    infer_capability_tags,
    match_capabilities,
    normalize_tags,
)
from alpha.swarm.models import SwarmPlan, SwarmTaskNode, TaskNodeState

logger = logging.getLogger(__name__)

#: Cap on a single specialist's task count when the roster does not say.
#: A specialist is one agent context: handing it thirty objectives is not
#: parallelism, it is a single long serial run wearing a team costume. When a
#: task cannot be placed because every eligible specialist is at its cap, it is
#: reported as unassigned-with-a-reason rather than queued forever.
DEFAULT_MAX_TASKS_PER_SPECIALIST = 4

#: ``SwarmTaskNode.worker_type`` for a node owned by a declared specialist.
#: Distinct from ``permanent_bot`` (a BotRegistry profile) and ``ephemeral``
#: (a bare one-shot model call) so a reader can tell the three apart on the
#: wire, and so an existing plan is unaffected by this constant existing.
SPECIALIST_WORKER_TYPE = "specialist"

#: Hard bound on roster size. A roster is a set of distinct agent identities;
#: a config typo that produced ten thousand entries is a config error, not a
#: team.
MAX_ROSTER_SIZE = 256

#: Hard bound on a specialist's declared capability tags, matching the existing
#: eligibility vocabulary's own ceiling.
MAX_SPECIALIST_TAGS = 64

#: Hard bound on the per-task objective slice handed to a specialist. The full
#: objective stays on the plan; the specialist gets a bounded, untrusted-data
#: framed copy.
MAX_SPECIALIST_TASK_CHARS = 8_000


class RosterConflictError(RuntimeError):
    """Raised when a second, different roster provider claims the roster.

    Two configuration owners silently overwriting each other is how a team ends
    up executing under a roster nobody is looking at. Registration is therefore
    fail-closed, while re-registering the *same* source is idempotent so a
    config hot-reload is not punished.
    """


@dataclass
class SpecialistProfile:
    """One declared specialist on a team.

    Satisfies the ``CapabilityProfile`` protocol of
    :mod:`alpha.capabilities.eligibility` structurally, so the existing
    hard-filter selection code works on it unchanged rather than a parallel
    reimplementation.

    ``capabilities`` and ``skills`` are both *offered* capabilities, matching
    :func:`~alpha.capabilities.eligibility.profile_capability_tags`: a skill is
    a thing the agent can actually do.
    """

    name: str
    role: str = ""
    capabilities: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    status: str = "active"
    #: A key of :data:`alpha.subagents.builtins.BUILTIN_SUBAGENTS`, the real
    #: differentiated subagent (own system prompt, own tool allowlist). This is
    #: what makes a specialist a specialist rather than a renamed generalist.
    agent_type: str | None = None
    #: Overrides the builtin system prompt when the catalogue wants a bespoke
    #: one. Ignored when ``agent_type`` is set to a builtin *and* no override is
    #: supplied -- the builtin prompt is the specialist's differentiation.
    system_prompt: str | None = None
    #: Tool-name allowlist handed to the specialist. ``None`` means "inherit the
    #: pool the runner assembled", which is deliberately the generous default;
    #: a catalogue that cares should name the tools.
    tools: list[str] | None = None
    #: ``None`` or ``"inherit"`` means the swarm's default model.
    model: str | None = None
    #: Optional per-member task cap, overriding
    #: :data:`DEFAULT_MAX_TASKS_PER_SPECIALIST`.
    max_tasks: int | None = None
    #: One line the operator reads: what this specialist is for.
    mission: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "mission": self.mission,
            "capabilities": list(self.capabilities),
            "skills": list(self.skills),
            "status": self.status,
            "agent_type": self.agent_type,
            "tools": list(self.tools) if self.tools is not None else None,
            "model": self.model,
            "max_tasks": self.max_tasks,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> SpecialistProfile:
        """Build a profile from one catalogue entry.

        ``id`` is accepted as an alias for ``name`` because configuration
        sections are keyed by id far more often than by a ``name`` field. A
        missing name is a hard error: a nameless specialist cannot be assigned,
        cannot be reported on, and cannot be denied.
        """

        name = str(data.get("name") or data.get("id") or "").strip()
        if not name:
            raise ValueError("a specialist entry must declare a non-empty 'name' (or 'id')")

        def _as_list(key: str) -> list[str]:
            raw = data.get(key)
            if raw is None:
                return []
            if isinstance(raw, str):
                return [raw]
            if isinstance(raw, Mapping):
                return [str(item) for item in raw]
            if isinstance(raw, Iterable):
                return [str(item) for item in raw]
            raise ValueError(f"specialist {name!r} field {key!r} must be a list, got {type(raw).__name__}")

        tools_raw = data.get("tools")
        return cls(
            name=name,
            role=str(data.get("role") or data.get("title") or "").strip(),
            mission=str(data.get("mission") or data.get("description") or "").strip(),
            capabilities=_as_list("capabilities")[:MAX_SPECIALIST_TAGS],
            skills=_as_list("skills")[:MAX_SPECIALIST_TAGS],
            status=str(data.get("status") or "active").strip().lower() or "active",
            agent_type=str(data.get("agent_type") or data.get("agent") or "").strip() or None,
            system_prompt=str(data.get("system_prompt") or "").strip() or None,
            tools=[str(item) for item in tools_raw] if isinstance(tools_raw, Iterable) and not isinstance(tools_raw, str) else None,
            model=str(data.get("model") or "").strip() or None,
            max_tasks=int(data["max_tasks"]) if isinstance(data.get("max_tasks"), int) and int(data["max_tasks"]) > 0 else None,
        )


#: A roster provider is any zero-argument callable returning a roster. The
#: return may be a sequence of :class:`SpecialistProfile`, a sequence of plain
#: mappings (so a config layer can hand over raw dicts without importing this
#: module), or a mapping of id -> entry.
RosterProvider = Callable[[], Sequence[SpecialistProfile] | Sequence[Mapping[str, Any]] | Mapping[str, Any]]

_ROSTER_LOCK = threading.RLock()
_roster_provider: RosterProvider | None = None
_roster_source: str = ""


def register_roster_provider(provider: RosterProvider, *, source: str = "") -> None:
    """Install the process-wide specialist roster provider.

    Idempotent for the same ``source`` (a config hot-reload re-registering is
    normal). A *different* source claiming an already-registered roster raises
    :class:`RosterConflictError` instead of silently taking over, because a
    team whose membership is decided by whichever loader ran last is not a team
    anybody can reason about.
    """

    if not callable(provider):
        raise TypeError("the specialist roster provider must be callable")
    source = str(source or "").strip()
    with _ROSTER_LOCK:
        global _roster_provider, _roster_source
        if _roster_provider is not None and _roster_source != source:
            raise RosterConflictError(
                f"a specialist roster is already registered by {_roster_source or '<unnamed source>'!r}; {source or '<unnamed source>'!r} may not replace it. Call unregister_roster_provider() first if the replacement is intentional."
            )
        _roster_provider = provider
        _roster_source = source


def unregister_roster_provider() -> None:
    """Remove the registered roster provider and return to the empty default.

    Used by config reload and by tests that need a clean slate. Exposed
    deliberately: a provider that can be installed must be removable, otherwise
    a test that registers a roster poisons every test after it.
    """

    with _ROSTER_LOCK:
        global _roster_provider, _roster_source
        _roster_provider = None
        _roster_source = ""


def registered_roster_source() -> str:
    """Return the source string of the registered provider, or ``""``."""

    with _ROSTER_LOCK:
        return _roster_source


def _empty_roster() -> tuple[list[SpecialistProfile], dict[str, Any]]:
    return [], {
        "registered": False,
        "source": "",
        "declared": 0,
        "accepted": 0,
        "rejected": [],
        "reason": ("no specialist roster provider is registered, so no task was assigned to a declared specialist. This is the pre-team behaviour, not a team that failed to form."),
    }


def _coerce_profiles(raw: Any) -> tuple[list[SpecialistProfile], list[dict[str, Any]]]:
    """Normalise a raw roster into profiles, isolating per-entry failures.

    A structural failure (the roster is a string, or a number) is fatal and is
    reported by the caller. A *single bad entry* is not: one nameless or
    malformed specialist in a 20-specialist roster must not cost the other 19.
    Each bad entry is returned as a rejection with the reason, which is what
    makes the "nameless" guard reachable at all -- before this, one missing
    ``name`` raised out of the whole roster and the per-entry reason was
    unreachable dead code.
    """

    if raw is None:
        return [], []
    if isinstance(raw, SpecialistProfile):
        return [raw], []
    if isinstance(raw, Mapping):
        entries: list[Any] = [dict(value) if isinstance(value, Mapping) else value for value in raw.values()]
    elif isinstance(raw, (str, bytes)):
        raise ValueError("a specialist roster must not be a bare string")
    elif isinstance(raw, Iterable):
        entries = list(raw)
    else:
        raise ValueError(f"a specialist roster must be a sequence or mapping, got {type(raw).__name__}")
    out: list[SpecialistProfile] = []
    rejected: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, SpecialistProfile):
            out.append(entry)
        elif isinstance(entry, Mapping):
            try:
                out.append(SpecialistProfile.from_mapping(entry))
            except ValueError as exc:
                rejected.append({"name": str(entry.get("name") or entry.get("id") or ""), "code": "nameless" if "name" in str(exc) else "malformed_entry", "detail": str(exc), "index": index})
        else:
            rejected.append({"name": "", "code": "malformed_entry", "detail": f"roster entry {index} must be a profile or a mapping, got {type(entry).__name__}", "index": index})
    return out, rejected


def resolve_roster(provider: RosterProvider | None = None) -> tuple[list[SpecialistProfile], dict[str, Any]]:
    """Return ``(roster, provenance)`` without ever raising.

    ``provenance`` says whether a roster was registered, who registered it, how
    many entries were declared, how many were accepted, and why any entry was
    rejected. A provider that raises is reported as a rejected roster with the
    error, so a broken catalogue degrades the plan instead of aborting plan
    construction -- but it is *reported*, never swallowed.
    """

    with _ROSTER_LOCK:
        active = provider if provider is not None else _roster_provider
        source = _roster_source if provider is None else "<explicit>"
    if active is None:
        return _empty_roster()
    try:
        raw = active()
    except Exception as exc:
        logger.exception("specialist roster provider %r failed", source or "<unnamed>")
        return [], {
            "registered": True,
            "source": source,
            "declared": 0,
            "accepted": 0,
            "rejected": [],
            "error": f"{type(exc).__name__}: {exc}",
            "reason": "the registered specialist roster provider raised, so no task was assigned to a declared specialist.",
        }

    provenance: dict[str, Any] = {
        "registered": True,
        "source": source,
        "declared": 0,
        "accepted": 0,
        "rejected": [],
    }
    try:
        profiles, entry_rejections = _coerce_profiles(raw)
    except ValueError as exc:
        return [], {**provenance, "error": str(exc), "reason": "the registered specialist roster is malformed, so no task was assigned to a declared specialist."}

    provenance["declared"] = len(profiles)
    provenance["rejected"] = list(entry_rejections)
    if len(profiles) > MAX_ROSTER_SIZE:
        rejected = [{"name": "", "code": "roster_too_large", "detail": f"roster declares {len(profiles)} specialists; the ceiling is {MAX_ROSTER_SIZE}"}]
        for profile in profiles[:MAX_ROSTER_SIZE]:
            rejected.append({"name": profile.name, "code": "roster_overflow", "detail": f"dropped: only the first {MAX_ROSTER_SIZE} specialists are accepted"})
        return profiles[:MAX_ROSTER_SIZE], {
            **provenance,
            "accepted": MAX_ROSTER_SIZE,
            "rejected": rejected,
            "reason": f"the roster declares more than {MAX_ROSTER_SIZE} specialists and was truncated; the overflow is listed in 'rejected'.",
        }

    accepted: list[SpecialistProfile] = []
    seen: dict[str, str] = {}
    for profile in profiles:
        key = profile.name.strip().lower()
        if not key:
            provenance["rejected"].append({"name": profile.name, "code": "nameless", "detail": "a specialist with no name cannot be assigned or reported on"})
            continue
        if key in seen:
            # Refuse rather than deduplicate: two entries claiming one identity
            # is an authoring mistake, and picking the first would make the
            # team a function of list order.
            provenance["rejected"].append({"name": profile.name, "code": "duplicate_name", "detail": f"'{profile.name}' is already declared; the duplicate was dropped, not merged"})
            continue
        seen[key] = profile.name
        accepted.append(profile)
    provenance["accepted"] = len(accepted)
    provenance["reason"] = f"{len(accepted)} declared specialist(s) from {source or '<unnamed source>'}" + (f"; {len(provenance['rejected'])} roster entr(ies) were rejected" if provenance["rejected"] else "")
    return accepted, provenance


@dataclass(frozen=True)
class CapabilityRequirement:
    """What a task needs, and how much authority that need carries.

    ``strict`` is the whole point of this class. A capability a caller
    *declared* on the task is a requirement: a specialist that cannot cover all
    of it is refused, and the task goes unassigned with the missing tags named.
    A capability *inferred* from the task's directive by a keyword vocabulary is
    a hint, and treating a four-tag heuristic conjunction as a hard conjunction
    makes every task ineligible for every specialist -- measured, not assumed: on
    a real goal that produced zero assignments out of five nodes. So an inferred
    requirement keeps a hard floor (a specialist must cover at least one of them;
    a specialist covering none is still refused) and ranks the survivors by
    coverage instead of excluding on any gap.
    """

    tags: frozenset[str] = frozenset()
    source: str = "none"
    strict: bool = False

    @property
    def constrained(self) -> bool:
        return bool(self.tags)

    def describe(self) -> str:
        if self.source == "declared":
            return f"declared on the task (all {len(self.tags)} tag(s) are required)"
        if self.source == "inferred":
            return f"inferred from the task's own directive as a hint ({sorted(self.tags)}); at least one must be covered, and coverage then ranks the survivors"
        return "unconstrained: the task's directive stated no capability, so no capability filter was applied and selection was not a capability match"


def required_capability_tags(task: SwarmTaskNode) -> CapabilityRequirement:
    """What capability does this task require, and how firm is that answer?

    Three sources, in order, each reported and never conflated:

    * the task *declares* ``capability_tags`` -- strict;
    * the task's tags were *inferred* from its own directive by the keyword
      vocabulary (``SwarmTaskNode.capability_source == "inferred"``) -- a hint,
      ranked not gated;
    * neither -- **unconstrained**, which is not the same as "matches
      everything" and is never reported as if it were a match.
    """

    declared = normalize_tags(task.capability_tags)
    if declared:
        if len(declared) > MAX_REQUIRED_TAGS:
            raise ValueError(f"task {task.task_id!r} declares more than {MAX_REQUIRED_TAGS} capability tags")
        source = "inferred" if task.capability_source == "inferred" else "declared"
        return CapabilityRequirement(tags=declared, source=source, strict=source == "declared")
    inferred = infer_capability_tags(task.objective)
    if inferred:
        return CapabilityRequirement(tags=inferred, source="inferred", strict=False)
    return CapabilityRequirement(tags=frozenset(), source="none", strict=False)


@dataclass
class TeamAssignment:
    """The recorded outcome of composing a team for one plan.

    Every drop is named. ``unassigned`` is the load-bearing field: it is the
    difference between "a team of specialists" and "we said it was a team".
    """

    roster_source: str = ""
    roster_registered: bool = False
    roster_provenance: dict[str, Any] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    unassigned: list[dict[str, Any]] = field(default_factory=list)
    load: dict[str, int] = field(default_factory=dict)

    @property
    def assigned_count(self) -> int:
        return sum(1 for decision in self.decisions if decision.get("assigned"))

    @property
    def unassigned_count(self) -> int:
        return len(self.unassigned)

    def to_dict(self) -> dict[str, Any]:
        return {
            "roster_registered": self.roster_registered,
            "roster_source": self.roster_source,
            "roster_provenance": dict(self.roster_provenance),
            "assigned": self.assigned_count,
            "unassigned": self.unassigned_count,
            "load": dict(sorted(self.load.items())),
            "decisions": [dict(item) for item in self.decisions],
            "unassigned_detail": [dict(item) for item in self.unassigned],
        }


def _cap_for(profile: SpecialistProfile, swarm_default: int | None) -> int:
    """The effective per-specialist task cap.

    A specialist that declares its own ``max_tasks`` is capped by the tighter of
    the two, never by the looser: a roster that says "I take at most one task"
    must not be overridden by a swarm-wide default of four.
    """

    if profile.max_tasks is not None:
        declared = max(1, int(profile.max_tasks))
        return declared if swarm_default is None else min(declared, max(1, int(swarm_default)))
    return max(1, int(DEFAULT_MAX_TASKS_PER_SPECIALIST if swarm_default is None else swarm_default))


def _select_specialist(
    task: SwarmTaskNode,
    roster: Sequence[SpecialistProfile],
    load: dict[str, int],
    *,
    swarm_default: int | None,
) -> tuple[SpecialistProfile | None, dict[str, Any]]:
    """Hard-filter the roster for one task and pick the best survivor.

    A **declared** requirement is resolved by the existing
    :func:`~alpha.capabilities.eligibility.eligible_candidates`, which is the
    one strict-coverage implementation in the codebase. An **inferred** one uses
    the existing :func:`~alpha.capabilities.eligibility.match_capabilities` per
    candidate and keeps only the specialists that cover at least one tag, so the
    hard property -- *a specialist that matches nothing is unreachable* -- holds
    in both tiers. Only the ranking differs.
    """

    requirement = required_capability_tags(task)
    reason = requirement.describe()
    if requirement.source == "declared":
        eligibility = eligible_candidates(roster, requirement.tags)
        survivors = [profile for profile in roster if profile.name.strip().lower() in set(eligibility.eligible)]
        coverage: dict[str, int] = {}
        rejected = [dict(item) for item in eligibility.rejected]
    else:
        coverage = {}
        rejected = []
        survivors = []
        for profile in roster:
            if str(getattr(profile, "status", "active")).strip().lower() not in ASSIGNABLE_BOT_STATUSES:
                rejected.append({"bot": profile.name, "code": "not_assignable", "detail": f"status '{profile.status}' cannot receive work"})
                continue
            match = match_capabilities(requirement.tags, profile) if requirement.constrained else None
            # The floor is `matched`, NOT `eligible`. ``match_capabilities``'s
            # ``eligible`` property is the *strict* rule -- it requires the
            # candidate to cover every tag -- so using it here silently
            # re-imposed the conjunction this two-tier design exists to relax,
            # and every multi-tag task came back "no specialist is eligible".
            if match is not None and not match.matched:
                rejected.append(
                    {
                        "bot": profile.name,
                        "code": "capability_mismatch",
                        "detail": f"covers none of the task's inferred tags: missing {sorted(match.missing)}",
                        "missing": sorted(match.missing),
                    }
                )
                continue
            coverage[profile.name] = len(match.matched) if match is not None else 0
            survivors.append(profile)

    within_cap = [profile for profile in survivors if load.get(profile.name, 0) < _cap_for(profile, swarm_default)]
    if not within_cap:
        detail: dict[str, Any] = {
            "task_id": task.task_id,
            "objective": task.objective,
            "required": sorted(requirement.tags),
            "requirement_source": requirement.source,
            "requirement_strict": requirement.strict,
            "requirement": reason,
            "eligible": [profile.name for profile in survivors],
            "reason": (
                f"every specialist that can take this task is already at its cap ({max(1, int(DEFAULT_MAX_TASKS_PER_SPECIALIST if swarm_default is None else swarm_default))} task(s) by default)"
                if survivors
                else "no specialist in the roster is eligible for this task"
            ),
            "rejected": rejected,
        }
        if survivors:
            detail["capped_out"] = [{"name": profile.name, "cap": _cap_for(profile, swarm_default), "assigned": load.get(profile.name, 0)} for profile in survivors]
        return None, detail
    chosen = min(within_cap, key=lambda profile: (-coverage.get(profile.name, 0), load.get(profile.name, 0), profile.name))
    return chosen, {
        "assigned": True,
        "task_id": task.task_id,
        "specialist": chosen.name,
        "required": sorted(requirement.tags),
        "requirement_source": requirement.source,
        "requirement_strict": requirement.strict,
        "requirement": reason,
        "cap": _cap_for(chosen, swarm_default),
        "coverage": coverage.get(chosen.name, 0),
        "eligible": [profile.name for profile in survivors],
        "selection": ("capability match is a hard filter; among survivors, highest coverage then lowest current task count, then name"),
        "rejected": rejected,
    }


def assign_specialists(
    plan: SwarmPlan,
    roster: Sequence[SpecialistProfile] | None = None,
    *,
    max_tasks_per_specialist: int | None = None,
) -> TeamAssignment:
    """Assign each task in *plan* to a declared specialist, or record why not.

    Writes only ownership fields on the task node: ``assigned_worker``,
    ``worker_type``, ``model_override`` and ``capability_tags``. It never writes
    ``state``, never writes a dependency, and never touches ``plan.mode`` or
    ``plan.metrics["strategy"]`` -- so the scheduler's DAG validation and the
    recorded-strategy invariant are untouched by composition.

    A task that cannot be assigned keeps whatever worker type it already had, so
    a plan whose catalogue is missing behaves exactly as it did before this
    module existed. What changes is that the plan now *says* so.
    """

    assignment = TeamAssignment()
    if roster is None:
        roster, provenance = resolve_roster()
    else:
        roster, provenance = list(roster), {"registered": True, "source": "<explicit>", "declared": len(roster), "accepted": len(roster), "rejected": []}
    assignment.roster_registered = bool(provenance.get("registered"))
    assignment.roster_source = str(provenance.get("source") or "")
    assignment.roster_provenance = dict(provenance)

    if not roster:
        assignment.roster_provenance.setdefault(
            "reason",
            "no specialist was available, so every task kept its default swarm worker and was not assigned to a declared specialist",
        )
        for task in plan.tasks.values():
            requirement = required_capability_tags(task)
            assignment.unassigned.append(
                {
                    "task_id": task.task_id,
                    "objective": task.objective,
                    "required": sorted(requirement.tags),
                    "requirement_source": requirement.source,
                    "requirement_strict": requirement.strict,
                    "requirement": requirement.describe(),
                    "eligible": [],
                    "reason": provenance.get("reason", "no specialist roster available"),
                    "rejected": [],
                }
            )
        return assignment

    load: dict[str, int] = {profile.name: 0 for profile in roster}
    for task in plan.tasks.values():
        chosen, detail = _select_specialist(task, roster, load, swarm_default=max_tasks_per_specialist)
        if chosen is None:
            assignment.unassigned.append(detail)
            continue
        load[chosen.name] = load.get(chosen.name, 0) + 1
        task.assigned_worker = chosen.name
        task.worker_type = SPECIALIST_WORKER_TYPE
        if chosen.model and chosen.model.strip().lower() != "inherit":
            task.model_override = chosen.model
        assignment.decisions.append(detail)
    assignment.load = {name: count for name, count in load.items() if count}
    return assignment


def _acceptance_label(task: SwarmTaskNode) -> str:
    """A one-word acceptance verdict, keeping execution and acceptance apart."""

    if task.verification.get("verified") is True or task.acceptance_status == "passed":
        return "accepted"
    if task.acceptance_status == "failed" or task.verification.get("verified") is False:
        return "acceptance_failed"
    if task.acceptance_criteria:
        return "unverified"
    return "not_required"


def _task_row(task: SwarmTaskNode) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "state": task.state.value if isinstance(task.state, TaskNodeState) else str(task.state),
        "assigned_to": task.assigned_worker,
        "worker_type": task.worker_type,
        "required_capabilities": sorted(task.capability_tags or []),
        "objective": task.objective,
        "result_summary": task.result_summary,
        "error": task.error_message,
        "attempts": task.attempts,
        "duration_seconds": task.duration_seconds,
        "acceptance": _acceptance_label(task),
        "acceptance_status": task.acceptance_status,
        "artifacts": list(task.output_artifacts),
        "evidence_count": len(task.evidence),
        "token_usage": dict(task.token_usage or {}),
        "tool_calls": task.tool_calls,
    }


def build_team_report(plan: SwarmPlan) -> dict[str, Any]:
    """Project a plan into the report an operator can actually read.

    Five questions, in the order an operator asks them: who was on the team,
    what each was asked, what each returned, what was accepted, and what is
    still unknown. The last one is not optional -- an operator reading a plan
    that reports only successes cannot tell a clean run from a hidden failure,
    which is the exact shape of the bug this repository keeps finding.

    The report is a pure projection. It creates no state, owns no lifecycle and
    can be recomputed from the plan at any time.
    """

    assignment = plan.metrics.get("team") if isinstance(plan.metrics, Mapping) else None
    assignment = dict(assignment) if isinstance(assignment, Mapping) else {}

    members: dict[str, dict[str, Any]] = {}
    unassigned: list[dict[str, Any]] = []
    for task in plan.tasks.values():
        row = _task_row(task)
        if task.worker_type == SPECIALIST_WORKER_TYPE and task.assigned_worker:
            entry = members.setdefault(
                task.assigned_worker,
                {
                    "name": task.assigned_worker,
                    "tasks": [],
                    "completed": 0,
                    "failed": 0,
                    "cancelled": 0,
                    "accepted": 0,
                    "acceptance_failed": 0,
                    "unverified": 0,
                    "tool_calls": 0,
                    "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                },
            )
            entry["tasks"].append(row)
            if row["state"] == TaskNodeState.COMPLETED.value:
                entry["completed"] += 1
            elif row["state"] == TaskNodeState.FAILED.value:
                entry["failed"] += 1
            elif row["state"] == TaskNodeState.CANCELLED.value:
                entry["cancelled"] += 1
            if row["acceptance"] == "accepted":
                entry["accepted"] += 1
            elif row["acceptance"] == "acceptance_failed":
                entry["acceptance_failed"] += 1
            elif row["acceptance"] == "unverified":
                entry["unverified"] += 1
            entry["tool_calls"] += int(row["tool_calls"] or 0)
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                entry["token_usage"][key] += int((row["token_usage"] or {}).get(key, 0) or 0)
        else:
            unassigned.append(row)

    for entry in members.values():
        entry["tasks"].sort(key=lambda item: item["task_id"])
    unassigned.sort(key=lambda item: item["task_id"])

    conflicts = [dict(item) for item in (assignment.get("conflicts") or [])]

    # "Unknown" is enumerated, never inferred from the absence of complaints.
    unknown: list[dict[str, Any]] = []
    if not members:
        unknown.append({"kind": "no_specialists_executed", "detail": "no task ran under a declared specialist; this report describes swarm execution, not a team of specialists"})
    elif all(entry["completed"] == 0 for entry in members.values()):
        unknown.append({"kind": "no_specialist_completed_work", "detail": "specialists were assigned but none of them completed a task; the team formed and produced nothing"})
    if not assignment:
        unknown.append(
            {
                "kind": "composition_not_recorded",
                "detail": "this plan carries no composition record, so the report cannot say why any task has no specialist; the tasks it lists are derived from plan state, not from an assignment decision",
            }
        )
    if not assignment.get("roster_registered"):
        unknown.append(
            {
                "kind": "no_roster_registered",
                "detail": "no specialist roster provider was registered when this plan was composed, so no task could be assigned to a declared specialist",
            }
        )
    for task in plan.tasks.values():
        if task.state == TaskNodeState.FAILED and task.error_message:
            unknown.append({"kind": "failed_task", "task_id": task.task_id, "detail": task.error_message})
        if task.acceptance_criteria and _acceptance_label(task) == "unverified":
            unknown.append({"kind": "acceptance_unverified", "task_id": task.task_id, "detail": "the task declared acceptance criteria and no deterministic verification verdict was recorded"})
    consensus = plan.consensus if isinstance(plan.consensus, Mapping) else {}
    if plan.requires_consensus and not consensus:
        unknown.append({"kind": "consensus_not_evaluated", "detail": "the plan requires consensus and none was evaluated"})
    elif consensus and consensus.get("status") not in (None, "approved"):
        unknown.append(
            {
                "kind": "consensus_not_approved",
                "detail": f"consensus status is {consensus.get('status')!r}: {consensus.get('reason')}",
                "deliberation_status": (consensus.get("deliberation") or {}).get("status") if isinstance(consensus.get("deliberation"), Mapping) else None,
            }
        )
    if conflicts:
        unknown.append({"kind": "unreconciled_conflicts", "count": len(conflicts), "detail": "specialists disagreed; no reconciliation was performed and no average was taken"})

    execution_status = plan.status
    return {
        "swarm_id": plan.swarm_id,
        "goal": plan.goal,
        "plan_mode": plan.mode.value if isinstance(plan.mode, str) else str(getattr(plan.mode, "value", plan.mode)),
        "execution_status": execution_status,
        "acceptance_status": ("accepted" if plan.tasks and all(_acceptance_label(task) == "accepted" for task in plan.tasks.values()) else "not_fully_accepted"),
        "roster": {
            "registered": bool(assignment.get("roster_registered")),
            "source": assignment.get("roster_source") or "",
            "reason": (assignment.get("roster_provenance") or {}).get("reason", "not composed"),
            "declared": (assignment.get("roster_provenance") or {}).get("declared", 0),
            "accepted": (assignment.get("roster_provenance") or {}).get("accepted", 0),
            "rejected": (assignment.get("roster_provenance") or {}).get("rejected", []),
        },
        "members": [members[name] for name in sorted(members)],
        # Two DIFFERENT lists, because they answer different questions and
        # conflating them is how a plan claims coverage it does not have:
        # `tasks_without_a_specialist` is derived from plan state and is
        # always correct; `unassigned_at_composition` is the assignment
        # decision's own record, carrying the reasons, and is empty when
        # composition was never run.
        "tasks_without_a_specialist": unassigned,
        "unassigned_at_composition": assignment.get("unassigned_detail", []),
        "conflicts": conflicts,
        "consensus": dict(consensus) if consensus else None,
        "unknown": unknown,
        "summary": {
            "total_tasks": len(plan.tasks),
            "specialist_tasks": sum(len(entry["tasks"]) for entry in members.values()),
            "completed": sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.COMPLETED),
            "failed": sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.FAILED),
            "cancelled": sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.CANCELLED),
            "not_started": sum(1 for task in plan.tasks.values() if task.state in (TaskNodeState.PENDING, TaskNodeState.QUEUED)),
            "members": len(members),
            "unassigned_at_composition": len(assignment.get("unassigned_detail", [])),
            "tasks_without_a_specialist": len(unassigned),
            "conflicts": len(conflicts),
            "unknown": len(unknown),
        },
        "report_basis": (
            "projection of SwarmPlan state recorded by the swarm runtime; a completed task means a worker "
            "returned a result, not that the result was verified, and 'accepted' reflects only the deterministic "
            "acceptance overlay, never a model's opinion of itself"
        ),
    }


def render_team_report_markdown(report: Mapping[str, Any]) -> str:
    """Render :func:`build_team_report` output as operator-readable Markdown.

    A separate function from the projection on purpose: the dictionary is the
    contract that tests and the wire read, and the prose is a rendering of it.
    A rendering bug can therefore never change what the report *says*.
    """

    summary = report.get("summary") or {}
    lines = [
        "# Team Report",
        "",
        f"- **Goal**: {report.get('goal')}",
        f"- **Plan mode**: `{report.get('plan_mode')}`",
        f"- **Execution status**: `{report.get('execution_status')}`",
        f"- **Acceptance status**: `{report.get('acceptance_status')}`",
        f"- **Roster**: {'registered from ' + str(report.get('roster', {}).get('source')) if report.get('roster', {}).get('registered') else 'none registered'}",
        f"- **Members on the team**: {summary.get('members', 0)}",
        f"- **Tasks**: {summary.get('completed', 0)} completed / {summary.get('failed', 0)} failed / {summary.get('cancelled', 0)} cancelled / {summary.get('not_started', 0)} not started (of {summary.get('total_tasks', 0)})",
        "",
    ]

    members = report.get("members") or []
    if members:
        lines.extend(["## The Team", "", "| Member | Asked | Completed | Failed | Accepted | Acceptance failed | Unverified | Tool calls |", "|---|---|---|---|---|---|---|---|"])
        for member in members:
            lines.append(f"| `{member['name']}` | {len(member['tasks'])} | {member['completed']} | {member['failed']} | {member['accepted']} | {member['acceptance_failed']} | {member['unverified']} | {member['tool_calls']} |")
        lines.append("")
    else:
        lines.extend(["## The Team", "", "- No task ran under a declared specialist.", ""])

    for member in members:
        lines.extend([f"### @{member['name']}", ""])
        for row in member["tasks"]:
            lines.append(f"- **`{row['task_id']}`** [{row['state']}] acceptance: `{row['acceptance']}`")
            lines.append(f"  - Asked: {row['objective']}")
            if row["result_summary"]:
                summary_text = row["result_summary"]
                lines.append(f"  - Returned: {summary_text if len(summary_text) <= 600 else summary_text[:600] + ' [truncated]'}")
            if row["error"]:
                lines.append(f"  - Error: {row['error']}")
            if row["artifacts"]:
                lines.append(f"  - Artifacts: {', '.join(row['artifacts'])}")
        lines.append("")

    unassigned = report.get("tasks_without_a_specialist") or []
    if unassigned:
        lines.extend(["## Tasks That Did Not Run Under a Declared Specialist", ""])
        for row in unassigned:
            lines.append(f"- **`{row['task_id']}`** ({row['state']}) asked for `{', '.join(row.get('required_capabilities') or []) or '(nothing declared)'}`")
        lines.append("")
    composition = report.get("unassigned_at_composition") or []
    if composition:
        lines.extend(["## Why No Specialist Could Take Them", ""])
        for row in composition:
            lines.append(f"- **`{row['task_id']}`** requires `{', '.join(row.get('required') or []) or '(nothing declared)'}` — {row.get('reason', '')}")
        lines.append("")

    conflicts = report.get("conflicts") or []
    if conflicts:
        lines.extend(["## Unreconciled Conflicts", ""])
        for conflict in conflicts:
            lines.append(f"- **{conflict.get('task_a')} vs {conflict.get('task_b')}**: {conflict.get('reason')}")
        lines.append("")

    consensus = report.get("consensus")
    if consensus:
        lines.extend(
            [
                "## Consensus",
                "",
                f"- **Status**: `{consensus.get('status')}` — {consensus.get('reason')}",
                f"- **Agreement**: {consensus.get('agreement') if consensus.get('agreement') is not None else 'unavailable'}",
            ]
        )
        deliberation = consensus.get("deliberation")
        if isinstance(deliberation, Mapping):
            guards = deliberation.get("guards") or []
            lines.append(f"- **Sequential deliberation**: `{deliberation.get('status')}`" + (f" (guards: {', '.join(str(g) for g in guards)})" if guards else ""))
        lines.append("")

    unknown = report.get("unknown") or []
    lines.extend(["## What Remains Unknown", ""])
    if unknown:
        for item in unknown:
            where = f" (`{item['task_id']}`)" if item.get("task_id") else ""
            lines.append(f"- **{item.get('kind')}**{where}: {item.get('detail')}")
    else:
        lines.append("- Nothing outstanding was recorded: every task reached a terminal state and every task that declared acceptance criteria carries a verdict.")
    lines.append("")
    lines.append(f"_{report.get('report_basis', '')}_")
    return "\n".join(lines)
