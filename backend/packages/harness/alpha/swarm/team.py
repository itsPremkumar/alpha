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
   it requires. Selection goes through the existing capability dispatch path
   (:func:`~alpha.bots.capability_dispatch.dispatch_task`) -- a hard filter,
   then a ranked contract-net auction -- so a specialist that does not declare
   the capability is unreachable however senior it looks, and composition
   carries no second matcher of its own.
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
layer. :func:`load_specialists_from_config` projects its active entries and
exact tool-group allowlists into this roster shape; the Gateway registers that
provider. This module owns the assignment policy, not the config parser:

    register_roster_provider(provider, source="...")

where ``provider`` is any zero-argument callable returning a roster. With no
registered provider or no enabled entries, :func:`resolve_roster` returns an
empty roster together with an explicit reason, and the plan keeps its default
worker types. That graceful degradation is deliberate: a missing catalogue
must be visible in the report without breaking plan construction.

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

from alpha.agents.middlewares.receipt_verification import VERDICT_SOURCE
from alpha.capabilities.eligibility import (
    MAX_REQUIRED_TAGS,
    eligible_candidates,
    infer_capability_tags,
    normalize_tags,
)
from alpha.swarm.grounding import GROUNDING_BASIS
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


def load_specialists_from_config(app_config: Any | None = None) -> list[SpecialistProfile]:
    """Project the operator-owned specialist catalogue into the swarm roster.

    Tool groups are translated to the exact configured tool names. This keeps
    the roster a projection of config.yaml and lets the worker enforce the
    same declaration as an actual SubagentConfig allowlist.
    """

    if app_config is None:
        from alpha.config import get_app_config

        app_config = get_app_config()
    catalog = getattr(app_config, "specialists", None)
    if catalog is None or not getattr(catalog, "enabled", False):
        return []

    profiles: list[SpecialistProfile] = []
    for entry in catalog.enabled_specialists():
        groups = set(entry.tool_groups or [])
        tool_names = sorted({str(tool.name) for tool in app_config.tools if getattr(tool, "group", None) in groups and getattr(tool, "name", None)})
        exclusions = "; ".join(entry.not_for)
        profile_prompt = (
            f"You are the {entry.title or entry.name} specialist ({entry.role}).\n"
            f"Mission: {entry.description}\n"
            f"Capabilities: {', '.join(entry.capabilities)}\n"
            f"Never handle: {exclusions}\n"
            "Use only your assigned tools. Report actions and evidence actually obtained; "
            "state clearly when a required action is unavailable."
        )
        profiles.append(
            SpecialistProfile(
                name=entry.name,
                role=entry.role,
                mission=entry.description,
                capabilities=list(entry.capabilities),
                skills=list(entry.skills),
                status="active",
                system_prompt=profile_prompt,
                tools=tool_names,
            )
        )
    return profiles


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


class _RosterBot:
    """A ``BotRegistry``-shaped view of one :class:`SpecialistProfile`.

    The capability dispatcher reads the registry protocol -- the shared
    eligibility vocabulary, the declared-fit scorer, the health monitor and the
    contract-net auction. Composition owns a roster of specialist profiles
    instead, so this projects one shape onto the other and the dispatch path
    stays the ONE implementation of "who takes this task". It is read-only and
    in-memory: the roster is a projection of configuration, never a second bot
    registry.
    """

    def __init__(self, profile: SpecialistProfile) -> None:
        self.profile = profile
        self.name = profile.name
        self.display_name = profile.name
        self.role = profile.role or profile.mission or profile.name
        self.department = "specialists"
        self.avatar = ""
        self.status = profile.status
        # ``reputation_score`` is a measured quantity and a roster entry does
        # not carry one; ``None`` becomes the dispatcher's disclosed neutral
        # 0.5 rather than an invented score.
        self.reputation_score = None
        # The registry shape calls prose capabilities "responsibilities"; a
        # specialist's mission is exactly that, so the declared-fit scorer can
        # read it. Declared capability tags stay in ``capabilities``/``skills``.
        self.responsibilities = [profile.mission] if profile.mission.strip() else []
        self.capabilities = list(profile.capabilities)
        self.skills = list(profile.skills)


class _RosterRegistry:
    """Read-only ``BotRegistry`` view over a composition roster."""

    def __init__(self, roster: Sequence[SpecialistProfile]) -> None:
        self._entries = [_RosterBot(profile) for profile in roster]
        self._by_name = {entry.name.strip().lower(): entry for entry in self._entries}

    def list_bots(self, include_archived: bool = False) -> list[_RosterBot]:
        # Status filtering is the shared eligibility vocabulary's job, applied
        # by the dispatcher; pre-filtering here would hide the rejection that
        # explains why a suspended specialist was skipped.
        return list(self._entries)

    def get_bot(self, name: str) -> _RosterBot | None:
        return self._by_name.get(str(name or "").strip().lower())


def _select_specialist(
    task: SwarmTaskNode,
    roster: Sequence[SpecialistProfile],
    load: dict[str, int],
    *,
    swarm_default: int | None,
) -> tuple[SpecialistProfile | None, dict[str, Any]]:
    """Dispatch one task to a specialist and record the outcome.

    Capability matching and winner selection are not re-implemented here: the
    task goes through :func:`~alpha.bots.capability_dispatch.dispatch_task`,
    the one production path for "a task is being assigned", over a read-only
    ``BotRegistry``-shaped view of this roster. A **declared** requirement is
    dispatched in ``"strict"`` mode (every tag is required); an **inferred** one
    is dispatched as a ranked hint, so the hard property -- *a specialist that
    covers none of the hints is unreachable* -- holds in both tiers while the
    ranking still differs.

    Composition supplies the three things the dispatcher cannot know -- the
    roster, the composition-level load ledger and the per-specialist caps --
    and turns a refusal into the composition-level reason, keeping the cap as
    the first explanation when every capable specialist was only excluded for
    being at it.
    """

    requirement = required_capability_tags(task)
    reason = requirement.describe()
    by_name = {profile.name.strip().lower(): profile for profile in roster}
    capped = [profile.name for profile in roster if load.get(profile.name, 0) >= _cap_for(profile, swarm_default)]
    # Local import: ``alpha.bots.capability_dispatch`` imports the swarm
    # contract-net module, whose package __init__ imports this module -- a
    # module-level import here would close the cycle.
    from alpha.bots.capability_dispatch import dispatch_task

    decision = dispatch_task(
        task.task_id,
        task.objective,
        required_capability_tags=sorted(requirement.tags) or None,
        registry=_RosterRegistry(roster),
        exclude=capped,
        requirement_mode="strict" if requirement.source == "declared" else "hint",
        # The roster is an operator-shipped team, not a leader's ad-hoc
        # delegation: its assignments are not bounded by the leader-side
        # ``DIRECTABLE_CAPABILITIES`` allowlist, which does not even carry tags
        # such as ``permission_audit``.
        enforce_authority=False,
    )

    if not decision.ok or decision.target is None:
        # Which specialists could have taken this task at all, ignoring the
        # cap exclusion: the same shared filter the dispatcher just ran, used
        # ONLY to say whether the refusal is "at cap" or "no capability".
        hint = requirement.source != "declared"
        capable = eligible_candidates(roster, requirement.tags, min_matches=1 if (hint and requirement.constrained) else 0)
        capable_profiles = [profile for profile in roster if profile.name.strip().lower() in set(capable.eligible)]
        all_capped = bool(capable_profiles) and all(load.get(profile.name, 0) >= _cap_for(profile, swarm_default) for profile in capable_profiles)
        detail: dict[str, Any] = {
            "task_id": task.task_id,
            "objective": task.objective,
            "required": sorted(requirement.tags),
            "requirement_source": requirement.source,
            "requirement_strict": requirement.strict,
            "requirement": reason,
            "eligible": [profile.name for profile in capable_profiles],
            "reason": (
                f"every specialist that can take this task is already at its cap ({max(1, int(DEFAULT_MAX_TASKS_PER_SPECIALIST if swarm_default is None else swarm_default))} task(s) by default)"
                if all_capped
                else f"no specialist in the composition roster can take this task: {decision.reason}"
            ),
            "rejected": [dict(item) for item in decision.rejected],
            "dispatch": {
                "outcome": decision.outcome.value,
                "refusal_code": decision.refusal_code,
                "method": decision.method,
                "requirement_mode": decision.requirement_mode,
                "ledger_seq": decision.ledger_seq,
            },
        }
        if all_capped:
            detail["capped_out"] = [{"name": profile.name, "cap": _cap_for(profile, swarm_default), "assigned": load.get(profile.name, 0)} for profile in capable_profiles]
        return None, detail

    chosen = by_name.get(str(decision.target).strip().lower())
    if chosen is None:
        return None, {
            "task_id": task.task_id,
            "objective": task.objective,
            "required": sorted(requirement.tags),
            "requirement_source": requirement.source,
            "requirement_strict": requirement.strict,
            "requirement": reason,
            "eligible": [],
            "reason": f"the dispatcher selected {decision.target!r}, which is not a profile on the composition roster",
            "rejected": [dict(item) for item in decision.rejected],
        }
    offered = normalize_tags([*chosen.capabilities, *chosen.skills])
    eligible_display = [profile.name for profile in roster if profile.name.strip().lower() in {str(item.get("bot_name") or "").strip().lower() for item in decision.candidates}]
    return chosen, {
        "assigned": True,
        "task_id": task.task_id,
        "specialist": chosen.name,
        "required": sorted(requirement.tags),
        "requirement_source": requirement.source,
        "requirement_strict": requirement.strict,
        "requirement": reason,
        "cap": _cap_for(chosen, swarm_default),
        "coverage": len(requirement.tags & offered),
        "eligible": eligible_display,
        "selection": ("capability dispatch: hard capability filter, declared-fit ranking, then a contract-net auction over relevance, capacity and cost; the handoff ledger holds the full recorded decision"),
        "rejected": [dict(item) for item in decision.rejected],
        "dispatch": {
            "outcome": decision.outcome.value,
            "refusal_code": decision.refusal_code,
            "method": decision.method,
            "requirement_mode": decision.requirement_mode,
            "ledger_seq": decision.ledger_seq,
        },
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
    receipt_audit = task.citation_audit
    citation_note = ""
    citation_status = ""
    if isinstance(receipt_audit, Mapping) and receipt_audit.get("source") == VERDICT_SOURCE:
        # Only an audit carrying the receipt-citation contract speaks. An empty
        # or foreign-shaped field means no audit was computed (receipts
        # unavailable, the audit failed, external completion, a legacy plan) —
        # reporting "unresolved" there would fabricate a negative verdict, and
        # honoring a bare ``citation_resolved`` from an unknown source would
        # fabricate a positive one.
        if receipt_audit.get("citation_resolved") is True:
            if receipt_audit.get("cited"):
                citation_status = "resolved"
                citation_note = "Receipt citations resolve to recorded calls; this does not prove claim correctness."
            # A resolved verdict citing nothing is a vacuous pass — nothing
            # was audited, so no citation line is rendered (same rule as
            # ``render_citation_verdict``).
        elif receipt_audit.get("no_citation_claims") is True:
            citation_status = "uncorroborated"
            citation_note = "Action claims were not supported by receipt citations."
        else:
            citation_status = "unresolved"
            citation_note = "One or more receipt citations did not resolve to a successful matching call."
    grounding_status = ""
    grounding_note = ""
    grounding_audit = task.claim_grounding
    if isinstance(grounding_audit, Mapping) and grounding_audit.get("basis") == GROUNDING_BASIS:
        # Same rule as the citation audit above: only an audit carrying the
        # grounding contract speaks. An empty or foreign-shaped field means no
        # grounding ran (receipts unavailable, external completion, a legacy
        # plan), and rendering a verdict there would fabricate one.
        checked_claims = grounding_audit.get("claims_checked")
        unresolved_claims = grounding_audit.get("unresolved")
        if isinstance(checked_claims, int) and checked_claims > 0:
            if isinstance(unresolved_claims, int) and unresolved_claims > 0:
                grounding_status = "unresolved_claims"
                grounding_note = f"{unresolved_claims} of {checked_claims} cited claim(s) did not resolve against the workspace; a cited path that cannot be corroborated is not accepted."
            else:
                grounding_status = "grounded"
                grounding_note = f"{checked_claims} cited claim(s) re-resolved against the workspace; this proves the cited paths exist, not that the claims are correct."
        # claims_checked == 0 renders no verdict, same as an audit that never ran.
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
        "citation_status": citation_status or None,
        "citation_verification": citation_note,
        "claim_grounding_status": grounding_status or None,
        "claim_grounding_detail": grounding_note,
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
        row = _task_row(task)
        if row["citation_status"] == "uncorroborated":
            unknown.append(
                {
                    "kind": "uncorroborated_action_claims",
                    "task_id": task.task_id,
                    "detail": row["citation_verification"],
                }
            )
        elif row["citation_status"] == "unresolved":
            unknown.append(
                {
                    "kind": "unresolved_receipt_citations",
                    "task_id": task.task_id,
                    "detail": row["citation_verification"],
                }
            )
        if row["claim_grounding_status"] == "unresolved_claims":
            unknown.append(
                {
                    "kind": "unresolved_claim_grounding",
                    "task_id": task.task_id,
                    "detail": row["claim_grounding_detail"],
                }
            )
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
            if row["citation_verification"]:
                lines.append(f"  - Receipt citation audit: {row['citation_verification']}")
            if row["claim_grounding_detail"]:
                lines.append(f"  - Claim grounding: {row['claim_grounding_detail']}")
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
            if row["citation_verification"]:
                lines.append(f"  - Receipt citation audit: {row['citation_verification']}")
            if row["claim_grounding_detail"]:
                lines.append(f"  - Claim grounding: {row['claim_grounding_detail']}")
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
