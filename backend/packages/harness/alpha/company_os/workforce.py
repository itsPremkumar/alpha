"""Workforce operations: turn a blueprint's planned hires into real agents.

The whole point of this module is that a "company employee" is **not a string**.
It is a real ``alpha.bots`` profile that exists in the shared roster, can be
messaged, can be killed, and can be reviewed — created through the registry's own
governed entry points so every existing ceiling, authority and lifecycle rule
still applies.

Three properties this module is responsible for:

* **Transactional.** A hire that fails part-way leaves nothing behind. The
  registry's own ``hire_bot`` is already transactional for its own steps; this
  adds the company-side employment records on top and rolls those back if a
  later hire in the same batch fails, so a half-built workforce cannot be
  mistaken for a working one.
* **Leader-scoped.** ``hire_bot``/``rescope_bot`` require an actor in
  ``SELF_EXTENSION_ACTORS``. The company acts as ``alpha``, and that string is
  never taken from a request body — see :data:`COMPANY_ACTOR`.
* **Honest reporting.** A hire is reported as created only after the profile
  exists in the registry and is readable. A refusal carries the registry's own
  reason rather than a generic failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from alpha.company_os.blueprints import Blueprint, PlannedEmployee
from alpha.company_os.models import (
    Company,
    Employment,
    EmploymentStatus,
    EmploymentType,
    now_ts,
)

logger = logging.getLogger(__name__)

#: The actor the company presents to the bot registry. This is a server-owned
#: constant, never caller input: it is the identity that satisfies
#: ``BotRegistry._assert_leader``. A request body cannot name itself ``alpha``.
COMPANY_ACTOR = "alpha"

#: Reason recorded on every profile this module creates, so a human reading the
#: roster can tell a company hire from a seed or template bot.
COMPANY_HIRE_REASON = "Hired as a company employee"


@dataclass
class HireOutcome:
    """The measured result of one hire attempt."""

    handle: str
    ok: bool
    #: ``True`` when the profile already existed and was reused rather than
    #: created. Reported separately so "hired 10" is never claimed when only 3
    #: profiles were actually created.
    already_existed: bool = False
    reason: str = ""
    rolled_back: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "ok": self.ok,
            "already_existed": self.already_existed,
            "reason": self.reason,
            "rolled_back": self.rolled_back,
        }


@dataclass
class HireReport:
    """Aggregate result of a batch, honest about partial success."""

    created: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    failed: list[HireOutcome] = field(default_factory=list)
    rolled_back: list[str] = field(default_factory=list)
    #: ``True`` when the batch was undone because of a failure.
    rolled_back_entirely: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed and not self.rolled_back_entirely

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "created": list(self.created),
            "reused": list(self.reused),
            "failed": [f.as_dict() for f in self.failed],
            "rolled_back": list(self.rolled_back),
            "rolled_back_entirely": self.rolled_back_entirely,
            "profiles_created_count": len(self.created),
        }


def _registry():
    """Resolve the real bot registry. Lazy so importing this module is cheap."""
    from alpha.bots.registry import get_bot_registry

    return get_bot_registry()


def agent_exists(handle: str) -> bool:
    """Whether a real bot profile with this handle exists."""
    try:
        return _registry().get_bot(handle) is not None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Bot registry unavailable while checking '%s': %s", handle, exc)
        return False


def describe_agent(handle: str) -> dict[str, Any] | None:
    """A small, honest projection of a real bot profile for the company UI.

    Returns ``None`` when the profile does not exist — which is a real answer
    the caller must render as "not hired", never as "no data".
    """
    try:
        bot = _registry().get_bot(handle)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Bot registry unavailable while describing '%s': %s", handle, exc)
        return None
    if bot is None:
        return None
    return {
        "handle": bot.name,
        "display_name": bot.display_name,
        "role": bot.role,
        "department": bot.department,
        "reports_to": bot.reports_to,
        "status": bot.status,
        "capabilities": list(bot.capabilities),
        "skills": list(bot.skills),
        "toolsets": list(bot.toolsets),
        "model": bot.model,
        "reputation_score": bot.reputation_score,
        "task_stats": dict(bot.task_stats or {}),
        "archived": bool(bot.archived_at),
        "version": bot.version,
        "created_at": bot.created_at,
        "updated_at": bot.updated_at,
    }


def hire_employee(company: Company, planned: PlannedEmployee) -> tuple[HireOutcome, Employment | None]:
    """Hire one planned employee, creating a real bot profile.

    Returns the outcome and the employment record to persist, or ``(outcome,
    None)`` when nothing was created. A caller that persists a partial record
    must treat the employment as absent when the outcome is not ok.
    """
    registry = _registry()
    handle = planned.handle.strip().lower()

    existing = registry.get_bot(handle)
    if existing is not None:
        # Reuse is not a failure, but it is also not a hire, so it is reported
        # separately rather than folded into the created count.
        employment = Employment(
            agent_handle=handle,
            employment_type=planned.employment_type,
            status=EmploymentStatus.ACTIVE,
            role_id=planned.role_id,
            unit_id=planned.unit_id,
            reports_to_handle=planned.reports_to_handle,
            backup_agent_handle=_first_backup(company, planned),
        )
        return HireOutcome(handle=handle, ok=True, already_existed=True, reason="Reused an existing bot profile."), employment

    # ``get_or_create`` would silently auto-provision a generic profile for an
    # unknown template, which is how a hire ends up with a placeholder role. The
    # template is therefore only passed when it actually resolves, and the hire
    # is refused rather than degraded when it does not.
    template = planned.bot_template
    if template:
        try:
            from alpha.bots.templates import get_template

            if get_template(template) is None:
                template = None
        except Exception:
            template = None

    try:
        # ``domain_tags`` are free-form role-matching labels and are deliberately
        # NOT passed here: ``enforce_grant`` treats every unrecognised string as a
        # violation, so sending "planning" or "security" would refuse the hire.
        # ``authority_grant`` is the real capability vocabulary, and ``None``
        # lets the registry apply its own default fleet grant.
        bot = registry.hire_bot(
            handle,
            actor=COMPANY_ACTOR,
            reason=COMPANY_HIRE_REASON,
            role=planned.role_title,
            requested_capabilities=planned.authority_grant,
            template=template,
        )
    except Exception as exc:
        # The registry raises for a malformed handle, an over-ceiling request, a
        # duplicate role, or an enforcement-machinery name. Its reason is the
        # useful part, so it is preserved verbatim.
        reason = f"{type(exc).__name__}: {exc}"
        logger.info("Company hire refused for '%s': %s", handle, reason)
        return HireOutcome(handle=handle, ok=False, reason=reason), None

    # ``hire_bot`` writes the profile but does not set org placement, so the
    # reporting line is applied afterwards. A failure here leaves a real profile
    # that reports to nobody, which is reported rather than hidden.
    placement_note = ""
    try:
        registry.update_bot(
            handle,
            department=planned.unit_name,
            reports_to=planned.reports_to_handle,
            responsibilities=list(planned.responsibilities),
        )
    except Exception as exc:
        placement_note = f"Org placement failed: {type(exc).__name__}: {exc}"

    if placement_note:
        logger.warning("Company hire of '%s' completed with a placement warning: %s", handle, placement_note)

    employment = Employment(
        agent_handle=bot.name,
        employment_type=planned.employment_type,
        status=EmploymentStatus.ACTIVE,
        role_id=planned.role_id,
        unit_id=planned.unit_id,
        reports_to_handle=planned.reports_to_handle,
        backup_agent_handle=_first_backup(company, planned),
    )
    reason = "Hired." if not placement_note else f"Hired with a warning: {placement_note}"
    return HireOutcome(handle=bot.name, ok=True, reason=reason), employment


def _first_backup(company: Company, planned: PlannedEmployee) -> str | None:
    """Pick a backup holder for a new hire: a peer in the same unit, else a lead."""
    for peer in company.employments:
        if peer.unit_id == planned.unit_id and peer.agent_handle != planned.handle and peer.is_active():
            return peer.agent_handle
    return None


def hire_blueprint(company: Company, blueprint: Blueprint, *, limit: int | None = None) -> HireReport:
    """Hire the blueprint's planned employees, transactionally.

    On the first hard failure every profile created *in this batch* is retired
    again, so a failed onboarding cannot leave a company that looks staffed but
    is missing half its team. Profiles that already existed are never touched.

    The company's own employment records are only committed when the whole batch
    succeeds; the caller persists them.
    """
    report = HireReport()
    created_in_batch: list[str] = []
    planned = list(blueprint.employees)
    if limit is not None and limit >= 0:
        planned = planned[:limit]

    for candidate in planned:
        outcome, _employment = hire_employee(company, candidate)
        if not outcome.ok:
            report.failed.append(outcome)
            if created_in_batch:
                report.rolled_back = _rollback(created_in_batch)
                report.rolled_back_entirely = True
            break
        if outcome.already_existed:
            report.reused.append(outcome.handle)
        else:
            report.created.append(outcome.handle)
            created_in_batch.append(outcome.handle)

    if report.ok:
        # Only commit the join records once every profile exists.
        for candidate in planned:
            handle = candidate.handle.strip().lower()
            if company.employment(handle) is None:
                _employment = Employment(
                    agent_handle=handle,
                    employment_type=candidate.employment_type,
                    status=EmploymentStatus.ACTIVE,
                    role_id=candidate.role_id,
                    unit_id=candidate.unit_id,
                    reports_to_handle=candidate.reports_to_handle,
                    backup_agent_handle=_first_backup(company, candidate),
                )
                company.employments.append(_employment)

        # Roles and units are *added*, not only merged: a company created from a
        # blueprint has neither yet, so an update-only pass would leave every
        # employment pointing at a unit id that does not exist.
        for role in blueprint.roles:
            if company.role(role.role_id) is None:
                company.roles.append(role)
        for unit in blueprint.units:
            existing = company.unit(unit.unit_id)
            if existing is None:
                company.units.append(unit)
            else:
                existing.lead_agent_handle = unit.lead_agent_handle or existing.lead_agent_handle
                if not existing.purpose:
                    existing.purpose = unit.purpose
        for duty in blueprint.accountabilities:
            company.accountabilities.append(duty)
        for objective in blueprint.objectives:
            company.objectives.append(objective)

    return report


def _rollback(handles: list[str]) -> list[str]:
    """Retire profiles created in this batch. Returns the handles actually retired."""
    registry = _registry()
    retired: list[str] = []
    for handle in handles:
        try:
            profile = registry.retire_bot(handle)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Rollback of '%s' failed: %s", handle, exc)
            continue
        if profile is not None:
            retired.append(handle)
        else:
            logger.error("Rollback of '%s' found no profile to retire", handle)
    return retired


def clone_employee(
    company: Company,
    source_handle: str,
    new_handle: str,
    *,
    display_name: str | None = None,
    role: str | None = None,
    department: str | None = None,
    reports_to: str | None = None,
    model: str | None = None,
) -> tuple[HireOutcome, Employment | None]:
    """Specialise an existing employee by cloning their profile.

    Cloning copies configuration, skills and SOUL but never memory, so the new
    hire starts with a clean history — which is the honest analogue of onboarding
    a specialist from a senior colleague's playbook.
    """
    registry = _registry()
    source = source_handle.strip().lower()
    target = new_handle.strip().lower()
    if not target:
        return HireOutcome(handle=target, ok=False, reason="A clone needs a non-empty handle."), None
    if registry.get_bot(source) is None:
        return HireOutcome(handle=target, ok=False, reason=f"Source agent '{source}' does not exist."), None

    source_employment = company.employment(source)
    unit_id = source_employment.unit_id if source_employment else None

    try:
        bot = registry.clone_bot(
            source,
            target,
            display_name=display_name,
            role=role,
            model=model,
            department=department,
            reports_to=reports_to if reports_to is not None else (source_employment.reports_to_handle if source_employment else None),
        )
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        logger.info("Company clone refused (%s -> %s): %s", source, target, reason)
        return HireOutcome(handle=target, ok=False, reason=reason), None

    employment = Employment(
        agent_handle=bot.name,
        employment_type=EmploymentType.EMPLOYEE,
        status=EmploymentStatus.ACTIVE,
        role_id=source_employment.role_id if source_employment else None,
        unit_id=unit_id,
        reports_to_handle=bot.reports_to,
        backup_agent_handle=_first_backup(company, PlannedEmployee(handle=bot.name, display_name=bot.display_name, role_title=bot.role, unit_name=bot.department)),
    )
    return HireOutcome(handle=bot.name, ok=True, reason=f"Cloned from '{source}'."), employment


def terminate_employee(company: Company, handle: str) -> HireOutcome:
    """Retire a real bot profile and mark the employment terminated.

    ``retire_bot`` is a soft delete that archives in place, so the profile stays
    readable as history — the right behaviour for a company record. The
    employment is marked terminated rather than removed so the org chart still
    shows who used to hold the role.
    """
    key = handle.strip().lower()
    employment = company.employment(key)
    registry = _registry()
    try:
        profile = registry.retire_bot(key)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning("Company terminate refused for '%s': %s", key, reason)
        return HireOutcome(handle=key, ok=False, reason=reason)
    if profile is None:
        return HireOutcome(handle=key, ok=False, reason=f"No bot profile named '{key}'.")

    employment = company.employment(key)
    if employment is not None:
        employment.status = EmploymentStatus.TERMINATED
        # Hand the role back so a departed employee does not leave a duty ownerless.
        if employment.backup_agent_handle:
            employment.backup_agent_handle = employment.backup_agent_handle

    # Re-point anything that reported to the departed employee.
    promoted = 0
    for other in company.employments:
        if other.reports_to_handle == key and other.is_active():
            other.reports_to_handle = employment.backup_agent_handle if employment else None
            promoted += 1

    for unit in company.units:
        if unit.lead_agent_handle == key:
            unit.lead_agent_handle = next(
                (e.agent_handle for e in company.active_employments() if e.unit_id == unit.unit_id and e.agent_handle != key),
                None,
            )

    for duty in company.accountabilities:
        if duty.active_agent_handle == key and duty.backup_agent_handle:
            duty.active_agent_handle = duty.backup_agent_handle
            duty.status = "failed_over"
            duty.failover_reason = f"{key} was terminated."
            duty.failover_at = now_ts()

    reason = "Retired." + (f" {promoted} reporting line(s) re-pointed." if promoted else "")
    return HireOutcome(handle=key, ok=True, reason=reason)


def promote_employee(company: Company, handle: str, *, new_role_id: str | None = None) -> HireOutcome:
    """Raise an employee's type or clearance.

    Promotion changes the *company* record. It deliberately does not widen the
    bot's authority grant: that is a separate ``rescope_bot`` call, and letting a
    promotion silently grant capabilities is exactly the escalation the registry's
    authority ceiling exists to refuse.
    """
    key = handle.strip().lower()
    employment = company.employment(key)
    if employment is None:
        return HireOutcome(handle=key, ok=False, reason=f"'{key}' is not an employee of this company.")

    if employment.employment_type is EmploymentType.EMPLOYEE:
        employment.employment_type = EmploymentType.MANAGER
    elif employment.employment_type is EmploymentType.MANAGER:
        employment.employment_type = EmploymentType.EXECUTIVE
        company.touch()
        return HireOutcome(
            handle=key,
            ok=True,
            reason="Promoted to executive. Its authority grant was NOT widened — grant capability separately.",
        )

    if new_role_id and company.role(new_role_id) is not None:
        employment.role_id = new_role_id
    company.touch()
    return HireOutcome(handle=key, ok=True, reason=f"Promoted to {employment.employment_type.value}.")


def record_review(company: Company, handle: str, *, score: float | None, note: str = "") -> HireOutcome:
    """Record a performance review for an employee.

    ``score`` is ``None`` when no measurement exists. It is never defaulted to a
    flattering number, because a review score that nobody measured is worse than
    an absent one: it reads as an assessment.
    """
    key = handle.strip().lower()
    employment = company.employment(key)
    if employment is None:
        return HireOutcome(handle=key, ok=False, reason=f"'{key}' is not an employee of this company.")
    from alpha.company_os.models import MeasurementBasis, now_ts

    employment.last_review_at = now_ts()
    if score is None:
        employment.performance_score = None
        employment.performance_basis = MeasurementBasis.UNMEASURED
        return HireOutcome(handle=key, ok=True, reason="Review recorded with no measured score.")
    employment.performance_score = float(score)
    employment.performance_basis = MeasurementBasis.MEASURED
    if note:
        employment.notes = note
    company.touch()
    return HireOutcome(handle=key, ok=True, reason=f"Review recorded at {score}.")


def sync_workforce_status(company: Company) -> dict[str, Any]:
    """Reconcile the company roster against the real bot registry.

    Reports the drift in both directions, because an employment whose profile is
    gone and a profile with no employment are different operational problems and
    a single boolean would hide the difference.
    """
    registry = _registry()
    missing_profiles: list[str] = []
    status_counts: dict[str, int] = {}

    for employment in company.employments:
        bot = registry.get_bot(employment.agent_handle)
        if bot is None:
            missing_profiles.append(employment.agent_handle)
            continue
        status_counts[bot.status] = status_counts.get(bot.status, 0) + 1

    employed = {e.agent_handle.strip().lower() for e in company.employments}
    unassigned = [b.name for b in registry.list_bots() if b.name.strip().lower() not in employed]

    return {
        "employment_count": len(company.employments),
        "active_employment_count": len(company.active_employments()),
        "missing_profile_count": len(missing_profiles),
        "missing_profiles": missing_profiles,
        "unassigned_bot_count": len(unassigned),
        "unassigned_bots": sorted(unassigned)[:25],
        "bot_status_counts": status_counts,
        "measured": True,
    }
