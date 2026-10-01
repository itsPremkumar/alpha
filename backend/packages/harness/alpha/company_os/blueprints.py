"""Organization blueprints: how a company turns a mission into a real org chart.

A blueprint is a *proposal*. Nothing here touches the bot registry, the Kanban
store, or the project store â€” the caller decides what to accept. Keeping
construction separate from application is what lets an operator preview the
whole structure, edit it, and then hire.

The bundled blueprints are derived from the archetype definitions that already
exist in :mod:`alpha.company.archetypes`, so the vocabulary ("company",
"open_source", "security_soc", "research_lab", "custom") stays in one place. A
blueprint is a projection of that archetype into the Company OS shape, not a
second archetype registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from alpha.company_os.models import (
    Accountability,
    AutonomyTier,
    Charter,
    ClearanceLevel,
    EmploymentType,
    KeyResult,
    Objective,
    ObjectiveStatus,
    OrgUnit,
    OrgUnitKind,
    Role,
)

#: Capability tiers granted to a role, from most to least privileged. The loop
#: checks the *acting* role's clearance before approving an action, so a worker
#: cannot approve its own restructure.
_EXEC_CLEARANCE = ClearanceLevel.L5_EXECUTIVE
_DIRECTOR_CLEARANCE = ClearanceLevel.L4_DIRECTOR
_LEAD_CLEARANCE = ClearanceLevel.L3_LEAD
_SPECIALIST_CLEARANCE = ClearanceLevel.L2_SPECIALIST
_WORKER_CLEARANCE = ClearanceLevel.L1_WORKER


@dataclass
class PlannedEmployee:
    """A proposed hire, before any bot profile exists."""

    #: Slug that will become the real ``alpha.bots`` profile name.
    handle: str
    display_name: str
    role_title: str
    unit_name: str
    reports_to_handle: str | None = None
    employment_type: EmploymentType = EmploymentType.EMPLOYEE
    clearance: ClearanceLevel = _WORKER_CLEARANCE
    responsibilities: list[str] = field(default_factory=list)
    #: The real ``alpha.bots`` template slug to hire against, when one exists.
    bot_template: str | None = None
    #: Free-form **domain tags** ("python", "research", "security") used to match
    #: this role to work in :func:`alpha.bots.work_discovery.match_bot_for_task`.
    #: These are deliberately NOT authority capabilities and are never sent to the
    #: registry's grant: feeding them there would make every unknown tag read as a
    #: violation. See :mod:`alpha.bots.authority_ceiling`.
    domain_tags: list[str] = field(default_factory=list)
    #: An optional **authority** grant from the real capability vocabulary
    #: (``observe``, ``reason``, ``dispatch``, ``workspace_write``,
    #: ``process_exec``). ``None`` means "let the registry default apply", which is
    #: the right answer for an ordinary hire.
    authority_grant: list[str] | None = None
    decision_authority: list[str] = field(default_factory=list)
    max_concurrent_assignments: int = 3
    # Filled in by :func:`build_blueprint` once unit and role ids are minted.
    role_id: str | None = None
    unit_id: str | None = None

    def preview(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "display_name": self.display_name,
            "role": self.role_title,
            "unit": self.unit_name,
            "reports_to": self.reports_to_handle,
            "employment_type": self.employment_type.value,
            "clearance": self.clearance.value,
            "bot_template": self.bot_template,
            "domain_tags": list(self.domain_tags),
            "authority_grant": list(self.authority_grant) if self.authority_grant else None,
            "decision_authority": list(self.decision_authority),
        }


@dataclass
class Blueprint:
    """A complete proposed organization, ready to preview and then apply."""

    name: str
    archetype: str
    description: str
    charter: Charter
    roles: list[Role] = field(default_factory=list)
    units: list[OrgUnit] = field(default_factory=list)
    employees: list[PlannedEmployee] = field(default_factory=list)
    accountabilities: list[Accountability] = field(default_factory=list)
    objectives: list[Objective] = field(default_factory=list)

    def preview(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "archetype": self.archetype,
            "description": self.description,
            "headcount": len(self.employees),
            "units": [{"name": u.name, "kind": u.kind.value, "lead": u.lead_agent_handle} for u in self.units],
            "employees": [e.preview() for e in self.employees],
            "accountabilities": [
                {
                    "title": a.title,
                    "primary": a.primary_agent_handle,
                    "backup": a.backup_agent_handle,
                    "escalation": a.escalation_agent_handle,
                }
                for a in self.accountabilities
            ],
            "objectives": [{"title": o.title, "key_results": [kr.title for kr in o.key_results]} for o in self.objectives],
        }


# --------------------------------------------------------------------------- #
# Handle derivation
# --------------------------------------------------------------------------- #


def slugify_handle(*parts: str) -> str:
    """Build a bot-handle-shaped slug from arbitrary text.

    Handles become real ``alpha.bots`` profile names, so they must satisfy the
    registry's own expectations: lowercase, alphanumeric with dashes, no
    leading or trailing separator.
    """
    joined = "-".join(p for p in parts if p)
    slug = re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        # A name made entirely of punctuation still needs a stable handle rather
        # than a crash, because this is derived from operator input.
        slug = "unit"
    if slug[0].isdigit():
        slug = f"x-{slug}"
    return slug[:64]


# --------------------------------------------------------------------------- #
# Blueprint construction
# --------------------------------------------------------------------------- #


def _make_roles(employees: list[PlannedEmployee]) -> list[Role]:
    """Mint one role per distinct job title, sharing a role across holders.

    Several people genuinely share a job, so a role is a job, not a person. This
    is what makes "who is accountable for this?" answerable: the role carries the
    authority, the employment carries the holder.
    """
    by_title: dict[str, Role] = {}
    for emp in employees:
        key = emp.role_title.strip().lower()
        role = by_title.get(key)
        if role is None:
            role = Role(
                title=emp.role_title,
                clearance=emp.clearance,
                responsibilities=list(emp.responsibilities),
                capabilities=list(emp.domain_tags),
                decision_authority=list(emp.decision_authority),
                max_concurrent_assignments=emp.max_concurrent_assignments,
                bot_template=emp.bot_template,
            )
            by_title[key] = role
        else:
            # Merge so a role shared by two holders keeps both scopes.
            for resp in emp.responsibilities:
                if resp not in role.responsibilities:
                    role.responsibilities.append(resp)
            for cap in emp.domain_tags:
                if cap not in role.capabilities:
                    role.capabilities.append(cap)
            for auth in emp.decision_authority:
                if auth not in role.decision_authority:
                    role.decision_authority.append(auth)
        emp.role_id = role.role_id
    return list(by_title.values())


def _make_units(employees: list[PlannedEmployee], unit_kinds: dict[str, OrgUnitKind]) -> list[OrgUnit]:
    """One org unit per distinct unit name, with its lead resolved from the plan."""
    units: list[OrgUnit] = []
    by_name: dict[str, OrgUnit] = {}
    for emp in employees:
        name = emp.unit_name.strip()
        if name not in by_name:
            unit = OrgUnit(
                name=name,
                kind=unit_kinds.get(name.lower(), OrgUnitKind.DEPARTMENT),
                lead_agent_handle=None,
                purpose="",
            )
            by_name[name] = unit
            units.append(unit)
        unit = by_name[name]
        emp.unit_id = unit.unit_id
        # The first planner listed for a unit is its lead, unless the blueprint
        # named an explicit lead for that unit.
        if unit.lead_agent_handle is None:
            unit.lead_agent_handle = emp.handle
    return units


def _wire_accountabilities(
    employees: list[PlannedEmployee],
    duties: list[tuple[str, str, str | None]],
) -> list[Accountability]:
    """Build failover chains from ``(duty, primary handle, backup handle)``.

    The escalation contact is the top of the reporting chain, so a duty can
    never end with nobody responsible even if both named holders fail.
    """
    executive = next(
        (e.handle for e in employees if e.employment_type is EmploymentType.EXECUTIVE),
        employees[0].handle if employees else "",
    )
    out: list[Accountability] = []
    for title, primary, backup in duties:
        if not primary:
            continue
        # The escalation contact is the top of the reporting chain, so a duty can
        # never end with nobody responsible. When the duty's primary *is* the
        # executive there is nobody above them, so the chain falls to any other
        # employee — a duty held only by the CEO with no backup is a single point
        # of failure, not a valid roster.
        escalation = executive if executive and executive != primary else None
        if escalation is None:
            escalation = next((e.handle for e in employees if e.handle != primary), None)
        out.append(
            Accountability(
                title=title,
                primary_agent_handle=primary,
                backup_agent_handle=backup or None,
                escalation_agent_handle=escalation,
                active_agent_handle=primary,
                status="active",
            )
        )
    return out


def build_blueprint(
    *,
    name: str,
    mission: str,
    archetype: str = "company",
    description: str = "",
    vision: str = "",
    values: list[str] | None = None,
    autonomy_tier: AutonomyTier = AutonomyTier.T1_ADVISE,
    budget_total_usd: float | None = None,
    budget_daily_usd: float | None = None,
    currency: str = "USD",
    employees: list[PlannedEmployee] | None = None,
    units: list[tuple[str, OrgUnitKind]] | None = None,
    accountabilities: list[tuple[str, str, str | None]] | None = None,
    objectives: list[tuple[str, list[tuple[str, float | None, float | None, str]]]] | None = None,
) -> Blueprint:
    """Assemble a complete proposed org chart.

    ``objectives`` is a list of ``(objective title, [(kr title, baseline,
    target, unit)])``. Baselines and targets are operator-declared, so the key
    results they create start at :attr:`MeasurementBasis.SEED` and their
    ``current`` stays ``None`` until the company measures something real.
    """
    planned = list(employees or [])
    unit_kinds = {name.lower(): kind for name, kind in (units or [])}

    role_list = _make_roles(planned)
    unit_list = _make_units(planned, unit_kinds)

    objective_list: list[Objective] = []
    for obj_title, key_results in objectives or []:
        krs: list[KeyResult] = []
        for kr_title, baseline, target, unit in key_results:
            kr = KeyResult(title=kr_title, baseline=baseline, target=target, current=None)
            owner_unit = next((u for u in unit_list if u.name.lower() == unit.lower()), None)
            if owner_unit is not None:
                kr.source_board_id = None
            krs.append(kr)
        obj = Objective(title=obj_title, key_results=krs, status=ObjectiveStatus.ACTIVE)
        owner_unit = next(
            (u for u in unit_list if any(emp.unit_id == u.unit_id and emp.employment_type is not EmploymentType.EMPLOYEE for emp in planned)),
            None,
        )
        obj.owner_unit_id = owner_unit.unit_id if owner_unit else (unit_list[0].unit_id if unit_list else None)
        objective_list.append(obj)

    return Blueprint(
        name=name,
        archetype=archetype,
        description=description,
        charter=Charter(
            mission=mission,
            vision=vision,
            values=list(values or []),
            autonomy_tier=autonomy_tier,
            budget_total_usd=budget_total_usd,
            budget_daily_usd=budget_daily_usd,
            currency=currency,
        ),
        roles=role_list,
        units=unit_list,
        employees=planned,
        accountabilities=_wire_accountabilities(planned, accountabilities or []),
        objectives=objective_list,
    )


# --------------------------------------------------------------------------- #
# Bundled blueprints
# --------------------------------------------------------------------------- #

#: Local copies of the archetype catalogue descriptions. ``alpha.company.archetypes``
#: owns the department-level definitions; this maps them onto Company OS roles
#: and the real ``alpha.bots`` template slugs, which the archetype module does
#: not know about.
_ARCHETYPE_CATALOGUE: list[dict[str, str]] = [
    {
        "archetype": "company",
        "display_name": "Autonomous AI Company & Startup",
        "description": "A full enterprise company: executive, product & engineering, security & SRE, and research & growth.",
    },
    {
        "archetype": "open_source",
        "display_name": "Open Source Maintainer Collective",
        "description": "Continuous repository maintenance, issue triage, PR review, SemVer releases, and documentation.",
    },
    {
        "archetype": "security_soc",
        "display_name": "24/7 Security Operations Center & Red/Blue Swarm",
        "description": "Continuous telemetry monitoring, CVE hunting, automated patch synthesis, and penetration testing.",
    },
    {
        "archetype": "research_lab",
        "display_name": "Autonomous Scientific & Discovery Lab",
        "description": "Literature crawling, hypothesis generation, simulation execution, and paper synthesis.",
    },
    {
        "archetype": "custom",
        "display_name": "Dynamic Custom Collective",
        "description": "A perpetual organization synthesized from any prompt â€” a game studio, data pipeline, newsroom, or trading desk.",
    },
]


def list_archetypes() -> list[dict[str, str]]:
    """The bundled organization archetypes."""
    return [dict(entry) for entry in _ARCHETYPE_CATALOGUE]


def _employee(
    handle: str,
    role: str,
    unit: str,
    *,
    template: str | None,
    reports_to: str | None,
    clearance: ClearanceLevel,
    responsibilities: list[str],
    capabilities: list[str] | None = None,
    authority: list[str] | None = None,
    employment_type: EmploymentType = EmploymentType.EMPLOYEE,
    max_assignments: int = 3,
) -> PlannedEmployee:
    return PlannedEmployee(
        handle=handle,
        display_name=handle.replace("-", " ").title(),
        role_title=role,
        unit_name=unit,
        reports_to_handle=reports_to,
        employment_type=employment_type,
        clearance=clearance,
        responsibilities=list(responsibilities),
        bot_template=template,
        domain_tags=list(capabilities or []),
        decision_authority=list(authority or []),
        max_concurrent_assignments=max_assignments,
    )


def _company_blueprint(name: str, mission: str, description: str, vision: str) -> list[PlannedEmployee]:
    """Executive â†’ Product & Engineering â†’ Security & SRE â†’ Research & Growth.

    The bot template slugs are the real ones from ``alpha.bots.templates``, so a
    hire lands on a profile that already has a sensible role, toolset and SOUL
    instead of a blank generic agent.
    """
    ceo = "ceo"
    return [
        _employee(
            ceo,
            "Executive Director",
            "Executive",
            template="ceo",
            reports_to=None,
            clearance=_EXEC_CLEARANCE,
            responsibilities=["Company strategy", "Budget allocation", "Governance", "Hiring and restructuring"],
            capabilities=["planning", "delegation", "governance"],
            authority=[
                "budget_approval",
                "department_synthesis",
                "strategic_pivot",
                "hire_terminate",
                "restructure",
                "emergency_halt",
            ],
            employment_type=EmploymentType.EXECUTIVE,
            max_assignments=4,
        ),
        _employee(
            "cto",
            "Lead Architect",
            "Product & Engineering",
            template="architect",
            reports_to=ceo,
            clearance=_DIRECTOR_CLEARANCE,
            responsibilities=["System architecture", "Technical standards", "Code review sign-off", "Tech stack selection"],
            capabilities=["planning", "code_review", "documentation"],
            authority=["architecture_sign_off", "rfc_approval", "tech_stack_selection"],
            max_assignments=5,
        ),
        _employee(
            "product-manager",
            "Product Manager",
            "Product & Engineering",
            template="researcher",
            reports_to="cto",
            clearance=_LEAD_CLEARANCE,
            responsibilities=["Requirements", "Prioritization", "Acceptance criteria", "Stakeholder synthesis"],
            capabilities=["research", "planning", "documentation"],
            authority=["backlog_prioritization"],
        ),
        _employee(
            "backend-lead",
            "Senior Backend Engineer",
            "Product & Engineering",
            template="coder",
            reports_to="cto",
            clearance=_SPECIALIST_CLEARANCE,
            responsibilities=["Backend services", "APIs", "Data layer"],
            capabilities=["coding", "testing"],
        ),
        _employee(
            "frontend-lead",
            "Frontend Engineer",
            "Product & Engineering",
            template="coder",
            reports_to="cto",
            clearance=_SPECIALIST_CLEARANCE,
            responsibilities=["Web workspace", "Interaction design", "Accessibility"],
            capabilities=["coding", "frontend"],
        ),
        _employee(
            "qa-lead",
            "QA & Verification Lead",
            "Security & SRE",
            template="tester",
            reports_to="cto",
            clearance=_LEAD_CLEARANCE,
            responsibilities=["Test strategy", "Regression verification", "Definition-of-Done enforcement"],
            capabilities=["testing", "code_review"],
            authority=["work_acceptance"],
        ),
        _employee(
            "security-lead",
            "Security Lead",
            "Security & SRE",
            template="security",
            reports_to=ceo,
            clearance=_DIRECTOR_CLEARANCE,
            responsibilities=["Threat modeling", "Vulnerability triage", "Security review"],
            capabilities=["security", "code_review"],
            authority=["security_sign_off", "release_block"],
        ),
        _employee(
            "sre-lead",
            "Site Reliability Engineer",
            "Security & SRE",
            template="sre",
            reports_to="cto",
            clearance=_LEAD_CLEARANCE,
            responsibilities=["Deployment", "Observability", "Incident response"],
            capabilities=["devops", "monitoring"],
        ),
        _employee(
            "research-lead",
            "Research Lead",
            "Research & Growth",
            template="researcher",
            reports_to=ceo,
            clearance=_LEAD_CLEARANCE,
            responsibilities=["Deep research", "Market intelligence", "Synthesis"],
            capabilities=["research", "documentation"],
        ),
        _employee(
            "support-lead",
            "Customer Support Lead",
            "Research & Growth",
            template="support",
            reports_to="cto",
            clearance=_SPECIALIST_CLEARANCE,
            responsibilities=["Inbound triage", "Customer questions", "Issue reproduction"],
            capabilities=["support", "documentation"],
        ),
    ]


def _open_source_blueprint() -> list[PlannedEmployee]:
    ceo = "maintainer-lead"
    return [
        _employee(
            ceo,
            "Maintainer Lead",
            "Leadership",
            template="ceo",
            reports_to=None,
            clearance=_EXEC_CLEARANCE,
            responsibilities=["Roadmap", "Release policy", "Escalation"],
            capabilities=["planning", "governance"],
            authority=["release_approval", "hire_terminate", "restructure"],
            employment_type=EmploymentType.EXECUTIVE,
        ),
        _employee(
            "issue-triage",
            "Issue Triage Engineer",
            "Issue Triage",
            template="support",
            reports_to=ceo,
            clearance=_SPECIALIST_CLEARANCE,
            responsibilities=["Issue ingestion", "Reproduction", "Labeling"],
            capabilities=["support", "testing"],
        ),
        _employee(
            "core-maintainer",
            "Core Maintainer",
            "Core Development",
            template="coder",
            reports_to=ceo,
            clearance=_LEAD_CLEARANCE,
            responsibilities=["Feature work", "Bug fixes", "Refactoring"],
            capabilities=["coding", "testing"],
            authority=["code_merge"],
        ),
        _employee(
            "pr-reviewer",
            "Code Reviewer",
            "Review & Security",
            template="reviewer",
            reports_to=ceo,
            clearance=_LEAD_CLEARANCE,
            responsibilities=["PR review", "Static analysis", "Upstream CVE audit"],
            capabilities=["code_review", "security"],
            authority=["work_acceptance"],
        ),
        _employee(
            "security-scanner",
            "Security Scanner",
            "Review & Security",
            template="security",
            reports_to="pr-reviewer",
            clearance=_SPECIALIST_CLEARANCE,
            responsibilities=["Dependency audit", "Secret scanning"],
            capabilities=["security"],
        ),
        _employee(
            "release-manager",
            "Release Manager",
            "Release & Docs",
            template="technical-writer",
            reports_to=ceo,
            clearance=_LEAD_CLEARANCE,
            responsibilities=["Semantic versioning", "Changelog synthesis", "Packaging"],
            capabilities=["documentation", "devops"],
            authority=["release_approval"],
        ),
        _employee(
            "doc-writer",
            "Technical Writer",
            "Release & Docs",
            template="technical-writer",
            reports_to="release-manager",
            clearance=_WORKER_CLEARANCE,
            responsibilities=["Documentation", "Examples", "Migration guides"],
            capabilities=["documentation"],
        ),
    ]


def _soc_blueprint() -> list[PlannedEmployee]:
    chief = "soc-lead"
    return [
        _employee(
            chief,
            "SOC Chief",
            "Command",
            template="ceo",
            reports_to=None,
            clearance=_EXEC_CLEARANCE,
            responsibilities=["Detection policy", "Escalation policy", "Incident command"],
            capabilities=["security", "governance"],
            authority=["incident_command", "hire_terminate"],
            employment_type=EmploymentType.EXECUTIVE,
        ),
        _employee(
            "telemetry-analyst",
            "Telemetry Analyst",
            "Blue Team",
            template="security",
            reports_to=chief,
            clearance=_SPECIALIST_CLEARANCE,
            responsibilities=["Log analysis", "Anomaly detection", "Threat feed monitoring"],
            capabilities=["security", "monitoring"],
        ),
        _employee(
            "red-lead",
            "Red Team Lead",
            "Red Team",
            template="security",
            reports_to=chief,
            clearance=_LEAD_CLEARANCE,
            responsibilities=["Penetration testing", "Exploit surface mapping"],
            capabilities=["security"],
            authority=["penetration_authorization"],
        ),
        _employee(
            "cve-hunter",
            "Vulnerability Researcher",
            "Patch Engineering",
            template="researcher",
            reports_to=chief,
            clearance=_LEAD_CLEARANCE,
            responsibilities=["CVE triage", "Patch synthesis", "Regression testing"],
            capabilities=["security", "coding", "research"],
            authority=["patch_sign_off"],
        ),
        _employee(
            "sandbox-verifier",
            "Patch Verifier",
            "Patch Engineering",
            template="tester",
            reports_to="cve-hunter",
            clearance=_SPECIALIST_CLEARANCE,
            responsibilities=["Isolated patch verification"],
            capabilities=["testing", "security"],
        ),
        _employee(
            "compliance-officer",
            "Compliance Officer",
            "Command",
            template="technical-writer",
            reports_to=chief,
            clearance=_SPECIALIST_CLEARANCE,
            responsibilities=["SOC2 evidence", "Audit trails", "Policy documentation"],
            capabilities=["documentation", "compliance"],
        ),
    ]


def _research_lab_blueprint() -> list[PlannedEmployee]:
    chief = "chief-scientist"
    return [
        _employee(
            chief,
            "Chief Scientist",
            "Leadership",
            template="ceo",
            reports_to=None,
            clearance=_EXEC_CLEARANCE,
            responsibilities=["Research direction", "Publication standards", "Funding narrative"],
            capabilities=["research", "governance"],
            authority=["publication_approval", "hire_terminate"],
            employment_type=EmploymentType.EXECUTIVE,
        ),
        _employee(
            "literature-crawler",
            "Literature Researcher",
            "Ingestion",
            template="researcher",
            reports_to=chief,
            clearance=_SPECIALIST_CLEARANCE,
            responsibilities=["Preprint ingestion", "Citation mapping", "SOTA tracking"],
            capabilities=["research", "web_search"],
        ),
        _employee(
            "hypothesis-engine",
            "Hypothesis Engineer",
            "Theory",
            template="architect",
            reports_to=chief,
            clearance=_LEAD_CLEARANCE,
            responsibilities=["Hypothesis formulation", "Experiment design"],
            capabilities=["research", "planning"],
            authority=["experiment_approval"],
        ),
        _employee(
            "simulation-runner",
            "Simulation Engineer",
            "Experimentation",
            template="coder",
            reports_to=chief,
            clearance=_SPECIALIST_CLEARANCE,
            responsibilities=["Simulation code", "Job execution", "Reproducibility"],
            capabilities=["coding", "testing"],
        ),
        _employee(
            "paper-writer",
            "Paper Writer",
            "Publication",
            template="technical-writer",
            reports_to=chief,
            clearance=_LEAD_CLEARANCE,
            responsibilities=["Manuscript preparation", "Figures", "Supplementary material"],
            capabilities=["documentation", "research"],
            authority=["manuscript_sign_off"],
        ),
        _employee(
            "peer-reviewer",
            "Internal Reviewer",
            "Publication",
            template="reviewer",
            reports_to=chief,
            clearance=_SPECIALIST_CLEARANCE,
            responsibilities=["Blind internal review", "Validity checks"],
            capabilities=["code_review", "research"],
        ),
    ]


_CUSTOM_DOMAIN_PROFILES: list[tuple[tuple[str, ...], str, str, str | None]] = [
    # (keywords, archetype label, default unit theme, template hint)
    (("game", "rpg", "engine", "physics", "graphics", "gameplay"), "Game Studio", "Game Development", "coder"),
    (("trading", "finance", "crypto", "arbitrage", "portfolio", "market"), "Trading Desk", "Trading", "coder"),
    (("data", "etl", "pipeline", "analytics", "streaming"), "Data Platform", "Data Engineering", "data-analyst"),
    (("news", "newsroom", "journalism", "media"), "Newsroom", "Editorial", "technical-writer"),
    (("ngo", "nonprofit", "charity", "non-profit"), "NGO", "Programs", "researcher"),
]


def _custom_blueprint(prompt: str) -> tuple[str, str, list[PlannedEmployee]]:
    """Synthesize a plausible org for any prompt.

    This is keyword-derived and makes no claim of understanding the domain; it
    produces a reasonable starting structure the operator then edits. The
    blueprint is a proposal, so being roughly right is acceptable and pretending
    to be exactly right would not be.
    """
    lowered = prompt.lower()
    label = "Custom Collective"
    unit_theme = "Operations"
    template_hint = "coder"
    for keywords, candidate_label, candidate_unit, candidate_template in _CUSTOM_DOMAIN_PROFILES:
        if any(word in lowered for word in keywords):
            label, unit_theme, template_hint = candidate_label, candidate_unit, candidate_template
            break

    stem = slugify_handle(prompt)[:24] or "collective"
    lead = f"{stem}-lead"
    return (
        label,
        unit_theme,
        [
            _employee(
                lead,
                f"{label} Lead",
                unit_theme,
                template="ceo",
                reports_to=None,
                clearance=_EXEC_CLEARANCE,
                responsibilities=["Direction", "Prioritization", "Escalation"],
                capabilities=["planning", "governance"],
                authority=["hire_terminate", "restructure", "strategic_pivot"],
                employment_type=EmploymentType.EXECUTIVE,
            ),
            _employee(
                f"{stem}-architect",
                "Solutions Architect",
                unit_theme,
                template="architect",
                reports_to=lead,
                clearance=_LEAD_CLEARANCE,
                responsibilities=["Design", "Interface contracts"],
                capabilities=["planning", "code_review"],
                authority=["architecture_sign_off"],
            ),
            _employee(
                f"{stem}-builder",
                "Implementation Engineer",
                unit_theme,
                template=template_hint,
                reports_to=f"{stem}-architect",
                clearance=_SPECIALIST_CLEARANCE,
                responsibilities=["Implementation", "Iteration"],
                capabilities=["coding", "testing"],
            ),
            _employee(
                f"{stem}-quality",
                "Quality Lead",
                "Quality & Risk",
                template="tester",
                reports_to=lead,
                clearance=_LEAD_CLEARANCE,
                responsibilities=["Verification", "Acceptance"],
                capabilities=["testing", "code_review"],
                authority=["work_acceptance"],
            ),
            _employee(
                f"{stem}-ops",
                "Operations Lead",
                "Operations",
                template="sre",
                reports_to=lead,
                clearance=_SPECIALIST_CLEARANCE,
                responsibilities=["Scheduling", "Monitoring", "Incident response"],
                capabilities=["devops", "monitoring"],
            ),
        ],
    )


def blueprint_for(
    *,
    name: str,
    mission: str,
    archetype: str | None = None,
    description: str = "",
    vision: str = "",
    values: list[str] | None = None,
    autonomy_tier: AutonomyTier = AutonomyTier.T1_ADVISE,
    budget_total_usd: float | None = None,
    budget_daily_usd: float | None = None,
    currency: str = "USD",
) -> Blueprint:
    """Build the bundled blueprint for an archetype, auto-detecting when omitted.

    Detection reuses :func:`alpha.company.archetypes.detect_archetype_from_prompt`
    so the keyword vocabulary has exactly one definition.
    """
    resolved = (archetype or "").strip().lower()
    if resolved not in {entry["archetype"] for entry in _ARCHETYPE_CATALOGUE}:
        try:
            from alpha.company.archetypes import detect_archetype_from_prompt

            resolved = detect_archetype_from_prompt(mission).value
        except Exception:
            resolved = "company"

    if resolved == "open_source":
        employees = _open_source_blueprint()
        blurb = _ARCHETYPE_CATALOGUE[1]["description"]
    elif resolved == "security_soc":
        employees = _soc_blueprint()
        blurb = _ARCHETYPE_CATALOGUE[2]["description"]
    elif resolved == "research_lab":
        employees = _research_lab_blueprint()
        blurb = _ARCHETYPE_CATALOGUE[3]["description"]
    elif resolved == "custom":
        label, unit_theme, employees = _custom_blueprint(mission)
        blurb = f"{label} synthesized from the supplied mission."
        name = name or label
    else:
        resolved = "company"
        employees = _company_blueprint(name, mission, description, vision)
        blurb = _ARCHETYPE_CATALOGUE[0]["description"]

    return build_blueprint(
        name=name,
        mission=mission,
        archetype=resolved,
        description=description or blurb,
        vision=vision,
        values=values,
        autonomy_tier=autonomy_tier,
        budget_total_usd=budget_total_usd,
        budget_daily_usd=budget_daily_usd,
        currency=currency,
        employees=employees,
        accountabilities=_default_duties(employees),
        objectives=_default_objectives(resolved),
    )


def _default_duties(employees: list[PlannedEmployee]) -> list[tuple[str, str, str | None]]:
    """Seed failover chains from each unit lead and their most senior report."""
    duties: list[tuple[str, str, str | None]] = []
    seen_units: set[str] = set()
    for emp in employees:
        if emp.unit_name in seen_units or not emp.unit_name:
            continue
        seen_units.add(emp.unit_name)
        backup = next(
            (r.handle for r in employees if r.unit_name == emp.unit_name and r.handle != emp.handle),
            None,
        )
        duties.append((f"{emp.unit_name} delivery", emp.handle, backup))
    return duties


def _default_objectives(archetype: str) -> list[tuple[str, list[tuple[str, float | None, float | None, str]]]]:
    """Seed objectives whose key results are declared targets, not measurements.

    ``baseline`` and ``target`` are operator intent. The resulting key results
    report ``current=None`` until the loop measures something, so a brand-new
    company never opens with invented progress.
    """
    presets: dict[str, list[tuple[str, list[tuple[str, float | None, float | None, str]]]]] = {
        "company": [
            (
                "Ship a reliable core platform",
                [
                    ("Deployment frequency", None, 5.0, "Product & Engineering"),
                    ("Core availability", None, 99.5, "Security & SRE"),
                ],
            ),
            (
                "Maintain a secure, reviewable codebase",
                [
                    ("Open high-severity findings", 0.0, 0.0, "Security & SRE"),
                    ("Work accepted on first review", None, 80.0, "Review & Security"),
                ],
            ),
        ],
        "open_source": [
            (
                "Keep contributor turnaround fast",
                [
                    ("PR review turnaround (hours)", None, 24.0, "Review & Security"),
                ],
            ),
            (
                "Hold quality above a floor",
                [
                    ("Test coverage", None, 85.0, "Core Development"),
                    ("Unresolved high-severity CVEs", 0.0, 0.0, "Review & Security"),
                ],
            ),
        ],
        "security_soc": [
            (
                "Detect and contain quickly",
                [
                    ("Mean time to detect (minutes)", None, 5.0, "Blue Team"),
                    ("Mean time to remediate (minutes)", None, 30.0, "Patch Engineering"),
                ],
            ),
        ],
        "research_lab": [
            (
                "Produce reproducible empirical results",
                [
                    ("Hypotheses validated per week", None, 5.0, "Theory"),
                    ("Simulation reproducibility", None, 95.0, "Experimentation"),
                ],
            ),
        ],
        "custom": [
            (
                "Deliver the stated objective continuously",
                [
                    ("Accepted work per week", None, 5.0, "Operations"),
                ],
            ),
        ],
    }
    return presets.get(archetype, presets["custom"])
