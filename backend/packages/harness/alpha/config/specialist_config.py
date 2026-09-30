"""The leader-authored specialist catalogue (``config.yaml`` -> ``specialists:``).

A **specialist** is a *declared* unit of expertise the rest of Alpha can
schedule. It is not a Bot, not a persona, and not a swarm plan. It is the
configuration that says "when work of this shape arrives, this is the role
that owns it, here is what it may touch, here is what it must ask about, and
here is the routing slot it runs on" — leader-authored, in ``config.yaml``,
validated at load.

Why this is a config section and not a Python table
---------------------------------------------------
The repository has one config file and a standing rule that a hand-maintained
table in Python is a second source of truth that drifts silently. The
per-Bot role table that already exists — :data:`alpha.bots.templates.BOT_TEMPLATES`
— is exactly such a table, and :data:`alpha.bots.permissions.DEFAULT_ROLE_RINGS`
is the executable half of it. This module therefore **references** those two
vocabularies and validates against them; it never restates a role, a
department, an avatar, a sandbox backend or a model.

The self-service boundary
-------------------------
``bot_roster`` lets a Bot edit ``display_name`` and ``avatar`` about itself and
refuses ``role``, ``model``, ``skills``, ``capabilities``, ``department`` and
``reports_to`` as leader-only (see ``_LEADER_ONLY`` in
``alpha/tools/builtins/bot_roster_tool.py``). Every field in this catalogue is
on the leader-only side of that line, so **the catalogue is leader-authored
configuration and is not a Bot self-edit surface at all**. Loading it is the
only way a specialist's role, capabilities or reporting line changes.

The thin-query rule is load-bearing
-----------------------------------
:func:`alpha.bots.forge.check_overlap` refuses a Bot whose job an existing Bot
already covers, but ``_overlap_score`` normalises against the *query's* own
ceiling — so a one-word role (``Security``) scores 1.0 against anything that
merely shares the word. The forge answers that by refusing to judge below
:data:`alpha.bots.survey.MIN_OVERLAP_TERMS` distinct significant terms. This
module imports that same floor: a specialist whose overlap query is thinner is
refused **at config load**, and a pair of specialists the forge's own check
would call duplicates is refused **at config load** too. A catalogue that
shipped a description the forge can never judge would be a description that
silently never protects anything.

What a specialist is NOT
------------------------
It grants nothing by itself. It is data. It does not mint a Bot, does not
bypass ``ToolPermissionGate``, does not bypass the authority ceiling, and does
not bypass the approval gate — it *feeds* them (see
:meth:`SpecialistConfig.to_forge_kwargs` and :meth:`SpecialistConfig.to_bot_template`).
The model for a specialist's run is still resolved through ``model_routing``;
a catalogue that named a vendor model id would be the mistake this repository
already made and removed, so :class:`SpecialistRouting` accepts a routing
**slot** and nothing else.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

__all__ = [
    "APPROVAL_POSTURES",
    "LEADER_ROLE_RING",
    "MIN_NOT_FOR_TERMS",
    "ROUTING_CATEGORIES",
    "ROUTING_TIERS",
    "SELECTION_OVERLAP_THRESHOLD",
    "SpecialistCatalogConfig",
    "SpecialistConfig",
    "SpecialistMatch",
    "SpecialistRouting",
    "get_specialist_catalog",
    "known_role_rings",
    "resolve_role_ring",
]


# ---------------------------------------------------------------------------
# Vocabulary mirrored from the routing tables (drift is pinned by tests)
# ---------------------------------------------------------------------------

#: Intent categories owned by :mod:`alpha.models.category_router`
#: (``DEFAULT_CATEGORY_SPECS``). Mirrored rather than imported so this module
#: stays import-light enough for ``AppConfig`` to import at module scope; the
#: import from ``alpha.models`` would cycle through the model factory.
#: ``tests/test_specialist_catalog.py::test_routing_vocabulary_matches_the_routers``
#: fails if the two ever diverge.
ROUTING_CATEGORIES: Final[tuple[str, ...]] = (
    "ultrabrain",
    "deep",
    "visual-engineering",
    "quick",
    "writing",
    "artistry",
    "unspecified-low",
    "unspecified-high",
)

#: Cost tiers owned by :mod:`alpha.models.workforce_router` (``ModelTier``).
ROUTING_TIERS: Final[tuple[str, ...]] = ("frontier", "coding", "fast", "local")

#: Approval postures. These are the forge's two states and no more:
#: ``draft_first`` composes :data:`alpha.bots.forge.DEFAULT_APPROVALS`,
#: ``autonomous`` is the deliberate ``approvals=[]`` opt-out. Both resolve
#: through :func:`resolve_approval_list`, so this catalogue cannot invent a
#: third posture the forge would not understand.
APPROVAL_POSTURES: Final[tuple[str, ...]] = ("draft_first", "autonomous")

#: A specialist handle is the Bot handle the forge would create, so it is held
#: to the forge's own rule (``[a-z0-9][a-z0-9_-]{0,63}``). Anything else would
#: produce a name ``forge_bot`` refuses after the catalogue already claimed it.
_HANDLE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")

#: Capability tags are slugs: they become ``BotProfile.capabilities`` and are
#: compared, not rendered as prose.
_CAPABILITY_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")

#: A one-word exclusion ("code") states nothing, so each ``not_for`` entry has
#: to say enough to be checkable.
MIN_NOT_FOR_TERMS: Final[int] = 2

#: Minimum overlap score a task must reach before a specialist is proposed.
#: The same number :func:`alpha.bots.forge.check_overlap` uses by default, so a
#: selection and a forge refusal agree on what "the same job" means.
SELECTION_OVERLAP_THRESHOLD: Final[float] = 0.34

ApprovalPosture = Literal["draft_first", "autonomous"]


# ---------------------------------------------------------------------------
# Role vocabulary — derived from the executable permission rings
# ---------------------------------------------------------------------------


#: The leader's own ring. It is a *narrow* ring — an explicit read/search/
#: message allowlist, denied every write, shell, push and deploy verb — so it
#: is not an ``allow_all`` escalation. It is still excluded from a specialist:
#: the leader dispatches work, a specialist performs it, and letting a
#: catalogue entry adopt the leader's identity would make the distinction
#: between the two unreadable at the point where it matters. Named here rather
#: than pattern-matched, so the exclusion is a reviewable line and not a guess.
LEADER_ROLE_RING: Final[str] = "autonomous leader & capability dispatch director"


def known_role_rings() -> dict[str, bool]:
    """``{ring_name: allow_all}`` for every declared role permission ring.

    Read live from :data:`alpha.bots.permissions.DEFAULT_ROLE_RINGS` so a
    specialist's ``role`` is checked against the table that actually decides
    which tools it gets. Imported lazily: ``alpha.bots`` pulls in the whole
    roster stack, which imports config.
    """
    from alpha.bots.permissions import DEFAULT_ROLE_RINGS

    return {name.lower(): bool(ring.allow_all) for name, ring in DEFAULT_ROLE_RINGS.items()}


def resolve_role_ring(role: str) -> tuple[str | None, str]:
    """Resolve ``role`` to the permission ring that will govern it.

    Deliberately a *subset* of ``ToolPermissionGate._resolve_ring``'s fuzzy
    containment match, restricted to the non-``allow_all`` worker rings. The
    gate resolves any role that contains a ring name (and resolves an exact
    ``lead``/``supervisor``/``admin`` to an unrestricted ring first); this
    resolver only answers "is there a worker ring in here?", so anything it
    accepts is a role the gate will also confine. When several rings match,
    the longest is reported — which one the gate *picks* is the gate's
    business and is unaffected by this function.

    Returns ``(ring_name_or_None, reason)``.
    """
    candidate = (role or "").strip().lower()
    if not candidate:
        return None, "role is empty"
    rings = known_role_rings()
    matches = sorted((name for name, allow_all in rings.items() if not allow_all and name not in {LEADER_ROLE_RING} and name in candidate), key=len, reverse=True)
    if not matches:
        privileged = ", ".join(sorted(name for name, allow_all in rings.items() if allow_all)) or "none"
        worker_rings = sorted(name for name, allow_all in rings.items() if not allow_all and name != LEADER_ROLE_RING)
        return None, (
            f"role '{role}' does not contain any worker permission ring. Declare it against one of "
            f"{', '.join(worker_rings)}, or add a ring for it. Privileged all-tools rings ({privileged}) and the "
            f"leader's own ring are not a specialist role."
        )
    return matches[0], f"role '{role}' resolves to the '{matches[0]}' permission ring"


# ---------------------------------------------------------------------------
# Routing intent
# ---------------------------------------------------------------------------


class SpecialistRouting(BaseModel):
    """A specialist's model **routing slot** — never a model name.

    ``category`` and ``tier`` are the keys ``config.yaml -> model_routing``
    already declares, which are themselves validated against ``models[]`` at
    load. A catalogue that hardcoded ``gpt-4o`` here would reintroduce exactly
    the failure the routing section exists to remove: a routing decision naming
    a model no operator has, resolved against nothing.
    """

    model_config = ConfigDict(extra="forbid")

    category: str | None = Field(
        default=None,
        description="Intent category key (see ROUTING_CATEGORIES). Resolved through model_routing.categories.",
    )
    tier: str | None = Field(
        default=None,
        description="Cost tier key (see ROUTING_TIERS). Resolved through model_routing.tiers.",
    )

    @field_validator("category", "tier")
    @classmethod
    def _known_slot(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        key = value.strip().lower()
        allowed = ROUTING_CATEGORIES if info.field_name == "category" else ROUTING_TIERS
        if key not in allowed:
            raise ValueError(f"specialists[].routing.{info.field_name} '{value}' is not a known routing slot. Expected one of: {', '.join(allowed)}")
        return key

    @model_validator(mode="after")
    def _at_least_one_slot(self) -> SpecialistRouting:
        if not self.category and not self.tier:
            raise ValueError("specialists[].routing declares neither a category nor a tier — omit the whole `routing:` block instead of an empty one")
        return self

    def slot_labels(self) -> list[str]:
        return [x for x in (f"category:{self.category}" if self.category else "", f"tier:{self.tier}" if self.tier else "") if x]


# ---------------------------------------------------------------------------
# One specialist
# ---------------------------------------------------------------------------


class SpecialistConfig(BaseModel):
    """One declared specialist.

    Every field here is leader-only configuration (see the module docstring's
    self-service boundary), and every field has a consumer in the existing team
    machinery — see ``docs/SPECIALISTS.md`` for the field-by-field table.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Stable specialist handle. Also the Bot handle the forge would create.")
    role: str = Field(description="Permission-ring-bearing role. Must resolve to a non-allow_all ring in alpha.bots.permissions.")
    description: str = Field(description="Prose that says when this specialist is the right one. Must clear the forge's thin-query floor.")
    not_for: list[str] = Field(description="Explicit negative scope: at least one thing this specialist must never be handed.")
    capabilities: list[str] = Field(description="Capability slugs. Become BotProfile.capabilities — what the specialist is offered.")
    title: str | None = Field(default=None, description="Human label for prompts and UI. Defaults to `name`.")
    tool_groups: list[str] = Field(default_factory=list, description="Config-declared tool groups. Become BotProfile.toolsets.")
    skills: list[str] = Field(default_factory=list, description="Skill names to load. Become BotProfile.skills.")
    approvals: ApprovalPosture = Field(default="draft_first", description="draft_first composes forge.DEFAULT_APPROVALS; autonomous is the deliberate empty-approvals opt-out.")
    extra_approvals: list[str] = Field(default_factory=list, description="Extra always-ask items added to the posture's list.")
    routing: SpecialistRouting | None = Field(default=None, description="Model routing intent: a declared model_routing slot, never a model name.")
    department: str | None = Field(default=None, description="Organisation department. Validated against alpha.bots.templates.DEPARTMENTS.")
    reports_to: str | None = Field(default=None, description="Another declared specialist's name. Becomes BotProfile.reports_to.")
    agent_preset: str | None = Field(default=None, description="Named agent preset. Validated against the built-in presets and config agent_presets.")
    sandbox: str | None = Field(default=None, description="Requested sandbox backend. Validated against alpha.bots.forge.SANDBOX_BACKENDS.")
    enabled: bool = Field(default=True, description="Whether this specialist participates in selection. Disabling keeps the declaration but takes it out of rotation.")

    # ---- shape validators: every message names the field -------------------

    @field_validator("name")
    @classmethod
    def _valid_handle(cls, value: str) -> str:
        handle = (value or "").strip().lower()
        if not handle:
            raise ValueError("specialists[].name is required")
        if not _HANDLE_RE.fullmatch(handle):
            raise ValueError(f"specialists[].name '{value}' is not a valid handle — use lowercase letters, digits, hyphen or underscore (max 64 chars), the same rule the forge enforces on a Bot name")
        return handle

    @field_validator("role")
    @classmethod
    def _resolvable_role(cls, value: str) -> str:
        role = (value or "").strip()
        if not role:
            raise ValueError("specialists[].role is required")
        _ring, reason = resolve_role_ring(role)
        if _ring is None:
            raise ValueError(f"specialists[].role: {reason}")
        return role

    @field_validator("capabilities", "tool_groups", "skills", "extra_approvals", "not_for")
    @classmethod
    def _clean_list(cls, value: list[str], info: Any) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value or []:
            if not isinstance(raw, str):
                raise ValueError(f"specialists[].{info.field_name} entries must be strings, got {type(raw).__name__}")
            item = raw.strip()
            if not item:
                # A blank entry is a hole in the declaration, not a wildcard.
                raise ValueError(f"specialists[].{info.field_name} contains an empty entry — remove it rather than declaring nothing")
            if item in seen:
                continue
            seen.add(item)
            cleaned.append(item)
        if info.field_name == "capabilities" and not cleaned:
            raise ValueError("specialists[].capabilities must declare at least one capability slug — a specialist with no declared capabilities is indistinguishable from a general worker")
        if info.field_name == "not_for" and not cleaned:
            raise ValueError("specialists[].not_for must declare at least one exclusion — a specialist that only says what it is good at cannot be told apart from its neighbours")
        return cleaned

    @field_validator("capabilities")
    @classmethod
    def _capability_slugs(cls, value: list[str]) -> list[str]:
        for item in value:
            if not _CAPABILITY_RE.fullmatch(item):
                raise ValueError(f"specialists[].capabilities entry '{item}' is not a slug — use lowercase letters, digits, dot, hyphen or underscore (max 64 chars)")
        return value

    @field_validator("department")
    @classmethod
    def _known_department(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from alpha.bots.templates import DEPARTMENTS

        key = value.strip().lower()
        if key not in DEPARTMENTS:
            raise ValueError(f"specialists[].department '{value}' is not a known department. Expected one of: {', '.join(DEPARTMENTS)}")
        return key

    @field_validator("sandbox")
    @classmethod
    def _known_sandbox(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from alpha.bots.forge import SANDBOX_BACKENDS

        key = value.strip().lower()
        if key not in SANDBOX_BACKENDS:
            raise ValueError(f"specialists[].sandbox '{value}' is not a forge sandbox backend. Expected one of: {', '.join(SANDBOX_BACKENDS)}")
        return key

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        return title or None

    # ---- the forge's own thin-query rule, applied at load -----------------

    @model_validator(mode="after")
    def _clears_the_thin_query_floor(self) -> SpecialistConfig:
        """Refuse a declaration the forge's overlap check can never judge.

        ``check_overlap`` returns its score as *evidence only* when the query
        holds fewer than :data:`alpha.bots.survey.MIN_OVERLAP_TERMS` distinct
        significant terms, because ``_overlap_score`` normalises against the
        query's own ceiling and a one-word role therefore matches everything.
        A specialist whose overlap query is that thin is a declaration whose
        duplicate-detection silently does nothing, so it is refused here
        rather than shipped.
        """
        from alpha.bots.survey import MIN_OVERLAP_TERMS, significant_terms

        terms = significant_terms(self.declared_prose())
        if len(terms) < MIN_OVERLAP_TERMS:
            raise ValueError(
                f"specialists[{self.name!r}] role+description yields only {len(terms)} significant term(s) "
                f"({sorted(terms)}); alpha.bots.survey.MIN_OVERLAP_TERMS is {MIN_OVERLAP_TERMS}. A description that thin "
                f"scores 1.0 against any other specialist that shares one word, so alpha.bots.forge.check_overlap would "
                f"refuse to judge it. Write the territory out."
            )
        for exclusion in self.not_for:
            exclusion_terms = significant_terms(exclusion)
            if len(exclusion_terms) < MIN_NOT_FOR_TERMS:
                raise ValueError(f"specialists[{self.name!r}].not_for entry '{exclusion}' has {len(exclusion_terms)} significant term(s); at least {MIN_NOT_FOR_TERMS} are required for an exclusion to be checkable")
        return self

    # ---- projections onto the existing team machinery ---------------------

    def declared_prose(self) -> str:
        """The operator-authored territory claim: role + description.

        This is the direct analogue of the forge's ``check_overlap(soul, role)``
        query, and the thing the thin-query floor is measured on. It is
        deliberately *not* the generated SOUL: the heading, the "Never handle
        this" label and the capability slugs are formatting this module
        invents, and counting them would let a one-word description clear the
        forge's floor on the strength of the generator's own output.
        """
        return "\n".join([self.role, self.description])

    def territory(self) -> str:
        """Everything this specialist claims competence at.

        ``role + description + capabilities`` — the positive claim only.
        ``not_for`` is deliberately **excluded**, and that is a measured
        decision rather than an oversight: the survey's ``_overlap_score``
        normalises the raw weight against the query's own ceiling, so folding
        exclusions in inflates the ceiling and depresses every real match. On
        the shipped team that turned a documentation task into a 0.43
        "security-reviewer" match purely because both entries mention a
        configuration change — one as competence, the other as a prohibition.
        An exclusion is not a claim of competence, so it must not be scored
        as one.
        """
        return "\n".join([self.role, self.description, *self.capabilities])

    def resolve_approval_list(self) -> list[str]:
        """The approval list the forge would bake into this specialist's SOUL.

        ``draft_first`` is the forge's :data:`alpha.bots.forge.DEFAULT_APPROVALS`;
        ``autonomous`` is the operator's explicit ``approvals=[]`` opt-out. The
        catalogue cannot produce a third answer, so a declaration cannot
        quietly lose the checkpoints the forge gives by default.
        """
        from alpha.bots.forge import DEFAULT_APPROVALS

        base: list[str] = list(DEFAULT_APPROVALS) if self.approvals == "draft_first" else []
        for item in self.extra_approvals:
            if item not in base:
                base.append(item)
        return base

    def to_soul(self) -> str:
        """The specialist's own persona text, before guardrails.

        :func:`alpha.bots.forge.build_guardrail_soul` appends the approval
        block, the escalation target and the sandbox note to whatever it is
        given, so this deliberately does not restate them.
        """
        lines = [f"# SOUL.md - {(self.title or self.name)} ({self.role})", "", self.description, ""]
        if self.not_for:
            lines.append("## Never handle this")
            lines.extend(f"- {item}" for item in self.not_for)
            lines.append("")
        if self.capabilities:
            lines.append("## Declared capabilities")
            lines.extend(f"- {item}" for item in self.capabilities)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def to_forge_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for :func:`alpha.bots.forge.forge_bot`.

        This is the whole reach of a specialist declaration: it produces the
        *arguments* to the existing transactional forge. It does not call the
        forge, does not create anything, and does not decide whether the build
        succeeds — the forge's own duplicate-role, routine-cost and sandbox
        pre-flight checks still run and can still refuse.
        """
        kwargs: dict[str, Any] = {
            "name": self.name,
            "role": self.role,
            "soul": self.to_soul(),
            "capabilities": list(self.capabilities),
            "approvals": self.resolve_approval_list(),
        }
        if self.tool_groups:
            kwargs["toolsets"] = list(self.tool_groups)
        if self.skills:
            kwargs["skills"] = list(self.skills)
        if self.department:
            kwargs["department"] = self.department
        if self.reports_to:
            kwargs["reports_to"] = self.reports_to
        if self.sandbox:
            kwargs["sandbox"] = self.sandbox
        return kwargs

    def to_bot_template(self) -> dict[str, Any]:
        """A ``.alphabot.json`` payload, keyed by the portable template fields.

        Uses :data:`alpha.bots.portable.TEMPLATE_FIELDS` so the projection
        cannot drift from what ``alpha.bots.portable.export_template`` is
        allowed to carry. Note what is *absent*: ``model`` (a template must not
        pin a vendor id) and anything credential-shaped.
        """
        from alpha.bots.portable import TEMPLATE_FIELDS

        payload = {
            "name": self.name,
            "display_name": self.title or self.name,
            "role": self.role,
            "soul": self.to_soul(),
            "department": self.department or "engineering",
            "reports_to": self.reports_to,
            "responsibilities": [self.description],
            "capabilities": list(self.capabilities),
            "skills": list(self.skills),
            "toolsets": list(self.tool_groups),
            "approvals": self.resolve_approval_list(),
            "sandbox": self.sandbox,
        }
        return {key: payload[key] for key in TEMPLATE_FIELDS if key in payload and payload[key] not in (None, [], {})}


@dataclass(frozen=True)
class SpecialistMatch:
    """One specialist proposed for a task, with the evidence for the score."""

    name: str
    role: str
    score: float
    terms: tuple[str, ...] = ()
    judgeable: bool = True
    """False when the task text was too thin to score honestly.

    Mirrors the forge: below :data:`alpha.bots.survey.MIN_OVERLAP_TERMS` the
    score is reported as evidence, not as a verdict.
    """

    specialist: SpecialistConfig | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "score": round(self.score, 3),
            "terms": list(self.terms),
            "judgeable": self.judgeable,
        }


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


class SpecialistCatalogConfig(BaseModel):
    """The ``specialists:`` section: a switch, an overlap switch, and entries."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Master switch. When false the declarations stay parsed and validated but no specialist is proposed.",
    )
    allow_overlap: bool = Field(
        default=False,
        description="Operator override for the duplicate-territory refusal, mirroring forge_bot(allow_overlap=...). Off means two specialists that describe the same job are a load error.",
    )
    entries: list[SpecialistConfig] = Field(
        default_factory=list,
        description="Declared specialists. Duplicate names, dangling reports_to, reporting cycles, and forge-refused duplicate territories are all load errors.",
    )

    @model_validator(mode="after")
    def _well_formed_catalogue(self) -> SpecialistCatalogConfig:
        seen: dict[str, int] = {}
        for index, entry in enumerate(self.entries):
            if entry.name in seen:
                raise ValueError(f"specialists.entries[{index}].name '{entry.name}' is already declared at entries[{seen[entry.name]}] — names are the specialist's identity and must be unique")
            seen[entry.name] = index

        known = set(seen)
        for index, entry in enumerate(self.entries):
            if entry.reports_to is not None and entry.reports_to not in known:
                raise ValueError(f"specialists.entries[{index}].reports_to '{entry.reports_to}' is not a declared specialist. Known: {', '.join(sorted(known)) or '<none>'}")
            if entry.reports_to == entry.name:
                raise ValueError(f"specialists.entries[{index}].reports_to points at the specialist itself")
        for entry in self.entries:
            self._reject_reporting_cycle(entry, known)

        if not self.allow_overlap:
            self._reject_duplicate_territories()
        return self

    def _reject_reporting_cycle(self, entry: SpecialistConfig, known: set[str]) -> None:
        """Refuse a loop, naming the whole path so the operator can find it.

        The walk carries the path it has taken rather than a bare visited-set:
        a two-node loop reports ``a -> b -> a`` instead of the useless
        "involving 'a' and 'a'".
        """
        path = [entry.name]
        current = entry.reports_to
        while current:
            # Membership is tested BEFORE the append: a node reached twice is a
            # loop, but the node we just arrived at is trivially "in path" and
            # checking after would report every single hop as a cycle.
            if current in path:
                cycle = " -> ".join([*path, current])
                raise ValueError(f"specialists: reporting cycle {cycle} — reports_to must be a hierarchy, not a loop")
            path.append(current)
            manager = self.get(current)
            if manager is None or manager.name not in known:
                return
            current = manager.reports_to

    def _reject_duplicate_territories(self) -> None:
        """Run the *forge's own* duplicate check across the declared team.

        :func:`alpha.bots.forge.check_overlap` is the rule that keeps a roster
        of twenty Bots from quietly becoming a roster of twenty overlapping
        ones. A catalogue that declared the same territory twice would forge
        into exactly that failure the moment an operator built both, so the
        check runs at load instead — the specialist that would be refused
        second is named, with the measured score.
        """
        from alpha.bots.forge import check_overlap

        for index, entry in enumerate(self.entries):
            existing = {other.name: other.territory() for other in self.entries if other.name != entry.name}
            if not existing:
                continue
            # The forge builds its query as ``role + soul``; ``territory()`` is
            # this specialist's equivalent claim, so the rule is applied to the
            # same shape of input the forge would actually see.
            role, _, claim = entry.territory().partition("\n")
            offender, score = check_overlap(
                claim,
                role,
                existing=existing,
                threshold=SELECTION_OVERLAP_THRESHOLD,
                name=entry.name,
            )
            if offender:
                raise ValueError(
                    f"specialists.entries[{index}] ('{entry.name}') and '{offender}' describe the same job "
                    f"(forge overlap {score:.2f} >= {SELECTION_OVERLAP_THRESHOLD}). alpha.bots.forge.check_overlap would "
                    f"refuse to build the second one, so a catalogue that declares both is refused here. Narrow the "
                    f"descriptions, or set specialists.allow_overlap: true."
                )

    # ---- lookups ---------------------------------------------------------

    def get(self, name: str) -> SpecialistConfig | None:
        key = (name or "").strip().lower()
        for entry in self.entries:
            if entry.name == key:
                return entry
        return None

    def enabled_specialists(self) -> list[SpecialistConfig]:
        """The specialists that may be scheduled right now.

        The section switch and the per-entry switch are separate on purpose: a
        disabled entry keeps its declaration (and its load-time validation)
        but leaves rotation.
        """
        if not self.enabled:
            return []
        return [entry for entry in self.entries if entry.enabled]

    def roots(self) -> list[SpecialistConfig]:
        """Enabled specialists with no manager — the top of the declared tree."""
        return [entry for entry in self.enabled_specialists() if not entry.reports_to]

    def chain(self, name: str) -> list[SpecialistConfig]:
        """The reporting chain from ``name`` upward, nearest manager first."""
        out: list[SpecialistConfig] = []
        seen: set[str] = set()
        current = self.get(name)
        while current is not None and current.name not in seen:
            seen.add(current.name)
            out.append(current)
            current = self.get(current.reports_to) if current.reports_to else None
        return out

    def reports(self, name: str) -> list[SpecialistConfig]:
        """Direct reports of ``name`` (the catalogue's ``get_subordinates``)."""
        key = (name or "").strip().lower()
        return [entry for entry in self.enabled_specialists() if entry.reports_to == key]

    # ---- selection -------------------------------------------------------

    def select_for_task(
        self,
        task: str,
        *,
        limit: int = 3,
        threshold: float = SELECTION_OVERLAP_THRESHOLD,
    ) -> list[SpecialistMatch]:
        """Propose specialists for ``task``, best first.

        Deterministic word matching — the same function the forge's overlap
        refusal and the workspace survey use (:func:`alpha.bots.survey.score_overlap`).
        No model call, no tokens, no network, so the same catalogue and the
        same task always produce the same order.

        A specialist scoring below ``threshold`` is **not** proposed: a weak
        match is worse than no match, because it reads as a routing decision.
        A thin *task* is reported rather than silently trusted, via
        :attr:`SpecialistMatch.judgeable`, using the same floor the forge uses.
        """
        from alpha.bots.survey import MIN_OVERLAP_TERMS, score_overlap, significant_terms

        query = (task or "").strip()
        if not query or limit <= 0:
            return []
        judgeable = len(significant_terms(query)) >= MIN_OVERLAP_TERMS
        matches: list[SpecialistMatch] = []
        for entry in self.enabled_specialists():
            score, terms = score_overlap(query, entry.territory())
            if score < threshold:
                continue
            matches.append(
                SpecialistMatch(
                    name=entry.name,
                    role=entry.role,
                    score=score,
                    terms=tuple(terms),
                    judgeable=judgeable,
                    specialist=entry,
                )
            )
        matches.sort(key=lambda m: (-m.score, m.name))
        return matches[:limit]

    def summary(self) -> list[dict[str, Any]]:
        """A JSON-safe projection, for a tool reply or an ops payload."""
        return [
            {
                "name": entry.name,
                "title": entry.title or entry.name,
                "role": entry.role,
                "department": entry.department,
                "reports_to": entry.reports_to,
                "capabilities": list(entry.capabilities),
                "tool_groups": list(entry.tool_groups),
                "skills": list(entry.skills),
                "approvals": entry.approvals,
                "approvals_ask_first": entry.resolve_approval_list(),
                "routing": entry.routing.slot_labels() if entry.routing else [],
                "enabled": entry.enabled,
            }
            for entry in self.entries
        ]


def get_specialist_catalog() -> SpecialistCatalogConfig:
    """The hot-reloaded catalogue from ``get_app_config()``.

    Import is deferred inside the function so this module stays importable
    from ``AppConfig`` at module scope.
    """
    from alpha.config.app_config import get_app_config

    return get_app_config().specialists
