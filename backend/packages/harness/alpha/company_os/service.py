"""The Company OS service: one owner-scoped facade over every operation.

The Gateway router talks only to this class. That is deliberate — it keeps the
tenant boundary in exactly one place, so no route can accidentally read another
owner's company, and it means the tenancy rule is testable without HTTP.

Every method takes ``owner_id`` explicitly. Nothing here reads an owner from a
request body, and nothing returns a company the caller does not own: an
ownerless read of someone else's company yields ``None``, which the route turns
into a 404 rather than a 403 that would confirm it exists.
"""

from __future__ import annotations

import logging
from typing import Any

from alpha.company_os import loop_safety, orchestrator, portfolio, workforce
from alpha.company_os.blueprints import blueprint_for, list_archetypes
from alpha.company_os.models import (
    AutonomyTier,
    Charter,
    Company,
    CompanyMetrics,
    CompanyState,
    MeasurementBasis,
    Objective,
    now_ts,
)
from alpha.company_os.store import CompanyNotFound, CompanyStore, get_company_store

logger = logging.getLogger(__name__)


class CompanyService:
    """Owner-scoped operations over a :class:`CompanyStore`."""

    def __init__(self, store: CompanyStore | None = None):
        self._store = store or get_company_store()

    @property
    def store(self) -> CompanyStore:
        return self._store

    # -- catalogue --------------------------------------------------------- #

    def archetypes(self) -> list[dict[str, str]]:
        """The bundled organization archetypes."""
        return list_archetypes()

    def preview_blueprint(self, *, name: str, mission: str, archetype: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Build a blueprint and return it as data, without creating anything.

        Previewing before committing is what makes the wizard safe: an operator
        sees the whole org chart, headcount and duties before any profile exists.
        """
        blueprint = blueprint_for(name=name, mission=mission, archetype=archetype, **kwargs)
        return {
            "blueprint": blueprint.preview(),
            "roles": [r.model_dump(mode="json") for r in blueprint.roles],
            "units": [u.model_dump(mode="json") for u in blueprint.units],
            "objectives": [o.model_dump(mode="json") for o in blueprint.objectives],
            "accountabilities": [a.model_dump(mode="json") for a in blueprint.accountabilities],
            "employees": [e.preview() for e in blueprint.employees],
        }

    # -- lifecycle --------------------------------------------------------- #

    def create_company(
        self,
        owner_id: str,
        *,
        name: str,
        mission: str,
        archetype: str | None = None,
        description: str = "",
        vision: str = "",
        values: list[str] | None = None,
        autonomy_tier: AutonomyTier | str = AutonomyTier.T1_ADVISE,
        budget_total_usd: float | None = None,
        budget_daily_usd: float | None = None,
        currency: str = "USD",
        hire: bool = True,
    ) -> tuple[Company, dict[str, Any]]:
        """Create a company, optionally hiring its workforce immediately.

        Returns the company and a hire report. ``hire=False`` creates an empty
        org the operator can staff later, which is a legitimate and common path.
        """
        tier = autonomy_tier if isinstance(autonomy_tier, AutonomyTier) else AutonomyTier(str(autonomy_tier))
        blueprint = blueprint_for(
            name=name,
            mission=mission,
            archetype=archetype,
            description=description,
            vision=vision,
            values=values,
            autonomy_tier=tier,
            budget_total_usd=budget_total_usd,
            budget_daily_usd=budget_daily_usd,
            currency=currency,
        )

        company = Company(
            owner_id=owner_id or "default",
            name=name,
            archetype=blueprint.archetype,
            description=blueprint.description,
            state=CompanyState.DRAFT,
            charter=blueprint.charter,
        )
        self._store.save(company, owner_id)

        if not hire:
            return company, {"skipped": True, "reason": "Workforce hiring was not requested."}

        report = workforce.hire_blueprint(company, blueprint)
        company.state = CompanyState.ACTIVE if report.ok else CompanyState.DEGRADED
        if budget_daily_usd is not None:
            company.cost.budget_daily_usd = budget_daily_usd
            company.loop_policy.daily_cost_ceiling_usd = budget_daily_usd
        self._store.save(company, owner_id)
        return company, report.summary()

    def get(self, company_id: str, owner_id: str) -> Company | None:
        return self._store.get(company_id, owner_id)

    def require(self, company_id: str, owner_id: str) -> Company:
        return self._store.require(company_id, owner_id)

    def list_companies(self, owner_id: str) -> list[Company]:
        companies: list[Company] = []
        for cid in self._store.list_company_ids(owner_id):
            company = self._store.get(cid, owner_id)
            if company is not None:
                companies.append(company)
        return sorted(companies, key=lambda c: c.created_at)

    def save(self, company: Company, owner_id: str) -> Company:
        return self._store.save(company, owner_id)

    def set_state(self, company_id: str, owner_id: str, state: CompanyState) -> Company:
        company = self.require(company_id, owner_id)
        company.state = state
        return self._store.save(company, owner_id)

    def archive(self, company_id: str, owner_id: str) -> Company:
        """Archive a company.

        Archiving is the normal removal path. ``store.delete`` exists for an
        operator explicitly discarding a record, and is not what a UI "delete"
        button should call.
        """
        company = self.require(company_id, owner_id)
        company.state = CompanyState.ARCHIVED
        company.archived_at = now_ts()
        company.loop_policy.enabled = False
        return self._store.save(company, owner_id)

    def update_charter(self, company_id: str, owner_id: str, **changes: Any) -> Company:
        company = self.require(company_id, owner_id)
        charter_updates: dict[str, Any] = {}
        for key in ("mission", "vision", "values", "constraints", "budget_total_usd", "budget_daily_usd", "currency"):
            if key in changes and changes[key] is not None:
                charter_updates[key] = changes[key]
        if "autonomy_tier" in changes and changes["autonomy_tier"] is not None:
            tier = changes["autonomy_tier"]
            charter_updates["autonomy_tier"] = tier if isinstance(tier, AutonomyTier) else AutonomyTier(str(tier))
        if charter_updates:
            company.charter = company.charter.model_copy(update=charter_updates)
            if "budget_daily_usd" in charter_updates:
                company.cost.budget_daily_usd = charter_updates["budget_daily_usd"]
                company.loop_policy.daily_cost_ceiling_usd = charter_updates["budget_daily_usd"]
        return self._store.save(company, owner_id)

    # -- objectives -------------------------------------------------------- #

    def add_objective(
        self,
        company_id: str,
        owner_id: str,
        *,
        title: str,
        rationale: str = "",
        key_results: list[dict[str, Any]] | None = None,
        owner_unit_id: str | None = None,
    ) -> Company:
        company = self.require(company_id, owner_id)
        from alpha.company_os.models import KeyResult, ObjectiveStatus

        objective = Objective(
            title=title,
            rationale=rationale,
            owner_unit_id=owner_unit_id,
            status=ObjectiveStatus.ACTIVE,
            key_results=[KeyResult(**kr) for kr in (key_results or [])],
        )
        company.objectives.append(objective)
        return self._store.save(company, owner_id)

    def delete_objective(self, company_id: str, owner_id: str, objective_id: str) -> Company:
        company = self.require(company_id, owner_id)
        company.objectives = [o for o in company.objectives if o.objective_id != objective_id]
        return self._store.save(company, owner_id)

    # -- workforce --------------------------------------------------------- #

    def hire(
        self,
        company_id: str,
        owner_id: str,
        *,
        handle: str,
        role_title: str,
        unit_name: str,
        reports_to_handle: str | None = None,
        bot_template: str | None = None,
        authority_grant: list[str] | None = None,
    ) -> tuple[Company, dict[str, Any]]:
        """Hire one employee into an existing company."""
        from alpha.company_os.blueprints import PlannedEmployee
        from alpha.company_os.models import OrgUnit
        from alpha.company_os.models import Role as RoleModel

        company = self.require(company_id, owner_id)
        unit = next((u for u in company.units if u.name.lower() == unit_name.lower()), None)
        if unit is None:
            unit = OrgUnit(name=unit_name)
            company.units.append(unit)

        role = next((r for r in company.roles if r.title.lower() == role_title.lower()), None)
        if role is None:
            role = RoleModel(title=role_title, bot_template=bot_template)
            company.roles.append(role)

        planned = PlannedEmployee(
            handle=handle,
            display_name=handle.replace("-", " ").title(),
            role_title=role_title,
            unit_name=unit.name,
            reports_to_handle=reports_to_handle,
            bot_template=bot_template,
            authority_grant=authority_grant,
            role_id=role.role_id,
            unit_id=unit.unit_id,
        )
        outcome, employment = workforce.hire_employee(company, planned)
        if outcome.ok and employment is not None:
            if company.employment(employment.agent_handle) is None:
                company.employments.append(employment)
            if unit.lead_agent_handle is None:
                unit.lead_agent_handle = employment.agent_handle
        self._store.save(company, owner_id)
        return company, outcome.as_dict()

    def clone_employee(self, company_id: str, owner_id: str, *, source: str, handle: str, **kwargs: Any) -> tuple[Company, dict[str, Any]]:
        company = self.require(company_id, owner_id)
        outcome, employment = workforce.clone_employee(company, source, handle, **kwargs)
        if outcome.ok and employment is not None and company.employment(employment.agent_handle) is None:
            company.employments.append(employment)
        self._store.save(company, owner_id)
        return company, outcome.as_dict()

    def terminate_employee(self, company_id: str, owner_id: str, handle: str) -> tuple[Company, dict[str, Any]]:
        company = self.require(company_id, owner_id)
        outcome = workforce.terminate_employee(company, handle)
        self._store.save(company, owner_id)
        return company, outcome.as_dict()

    def promote_employee(self, company_id: str, owner_id: str, handle: str, new_role_id: str | None = None) -> tuple[Company, dict[str, Any]]:
        company = self.require(company_id, owner_id)
        outcome = workforce.promote_employee(company, handle, new_role_id=new_role_id)
        self._store.save(company, owner_id)
        return company, outcome.as_dict()

    def review_employee(self, company_id: str, owner_id: str, handle: str, *, score: float | None, note: str = "") -> tuple[Company, dict[str, Any]]:
        company = self.require(company_id, owner_id)
        outcome = workforce.record_review(company, handle, score=score, note=note)
        self._store.save(company, owner_id)
        return company, outcome.as_dict()

    # -- portfolio --------------------------------------------------------- #

    def attach_project(self, company_id: str, owner_id: str, *, project_id: str, title: str = "", objective_ids: list[str] | None = None) -> Company:
        company = self.require(company_id, owner_id)
        portfolio.attach_project(company, project_id, title=title, objective_ids=objective_ids)
        return self._store.save(company, owner_id)

    def create_work_item(
        self,
        company_id: str,
        owner_id: str,
        *,
        title: str,
        description: str = "",
        priority: str = "normal",
        assignee_agent_handle: str | None = None,
        definition_of_done: list[str] | None = None,
        objective_id: str | None = None,
    ) -> tuple[Company, dict[str, Any]]:
        company = self.require(company_id, owner_id)
        try:
            link = portfolio.create_work_item(
                company,
                title=title,
                description=description,
                priority=priority,
                assignee_agent_handle=assignee_agent_handle,
                definition_of_done=definition_of_done,
                objective_id=objective_id,
            )
        except Exception as exc:
            logger.info("Work item creation refused for %s: %s", company_id, exc)
            return company, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self._store.save(company, owner_id)
        return company, {"ok": True, "work_item": link.model_dump(mode="json")}

    def create_group(
        self,
        company_id: str,
        owner_id: str,
        *,
        name: str,
        topic: str = "",
        members: list[str] | None = None,
    ) -> tuple[Company, dict[str, Any]]:
        company = self.require(company_id, owner_id)
        try:
            link = portfolio.create_group(company, name=name, topic=topic, members=members)
        except Exception as exc:
            logger.info("Group creation refused for %s: %s", company_id, exc)
            return company, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self._store.save(company, owner_id)
        return company, {"ok": True, "group": link.model_dump(mode="json")}

    def announce(self, company_id: str, owner_id: str, *, room_id: str, sender: str, content: str) -> dict[str, Any]:
        """Post a company announcement to a real group room."""
        company = self.require(company_id, owner_id)
        known = {g.room_id for g in company.groups}
        if room_id not in known:
            return {"ok": False, "error": f"Room '{room_id}' is not part of this company."}
        return portfolio.post_group_message(company, room_id, sender=sender, content=content)

    def attach_schedule(self, company_id: str, owner_id: str, **kwargs: Any) -> Company:
        company = self.require(company_id, owner_id)
        portfolio.attach_schedule(company, **kwargs)
        return self._store.save(company, owner_id)

    # -- loop -------------------------------------------------------------- #

    def run_tick(self, company_id: str, owner_id: str) -> TickRecordView:
        """Run one loop tick, persist its ledger entry, and return the view."""
        company = self.require(company_id, owner_id)
        record = orchestrator.step(company)
        self._store.append_tick(record)
        self._store.save(company, owner_id)
        return TickRecordView(record=record, company=company)

    def loop_history(self, company_id: str, owner_id: str, limit: int = 25) -> list[dict[str, Any]]:
        self.require(company_id, owner_id)
        return [r.model_dump(mode="json") for r in self._store.list_ticks(company_id, owner_id, limit=limit)]

    def set_loop_policy(self, company_id: str, owner_id: str, **changes: Any) -> Company:
        from alpha.company_os.models import LoopPolicy

        company = self.require(company_id, owner_id)
        sanitized = {k: v for k, v in changes.items() if v is not None and k in LoopPolicy.model_fields}
        if sanitized:
            company.loop_policy = company.loop_policy.model_copy(update=sanitized)
        self._store.save(company, owner_id)
        return company

    def approve(self, company_id: str, owner_id: str, approval_id: str, *, note: str = "") -> tuple[Company, dict[str, Any]]:
        """Approve a pending proposal.

        Approving is the only path by which a proposal becomes real, and it is an
        explicit owner-scoped mutation. A rejected proposal is kept, not deleted,
        so a later reviewer sees what was proposed and why it was declined.
        """
        from alpha.company_os.models import ApprovalKind, ApprovalStatus

        company = self.require(company_id, owner_id)
        target = next((a for a in company.approvals if a.approval_id == approval_id), None)
        if target is None:
            return company, {"ok": False, "error": f"No approval '{approval_id}' on this company."}
        if target.status is not ApprovalStatus.PENDING:
            return company, {"ok": False, "error": f"Approval '{approval_id}' is already {target.status.value}."}

        applied = {"approved": False, "detail": ""}
        if target.kind is ApprovalKind.HIRE:
            payload = target.payload or {}
            handle = str(payload.get("handle") or "").strip().lower()
            if handle:
                try:
                    from alpha.company_os.blueprints import PlannedEmployee

                    outcome, _emp = workforce.hire_employee(
                        company,
                        PlannedEmployee(
                            handle=handle,
                            display_name=handle.replace("-", " ").title(),
                            role_title=str(payload.get("role") or "Specialist"),
                            unit_name=str(payload.get("unit") or "General"),
                            bot_template=payload.get("bot_template"),
                        ),
                    )
                    applied = {"approved": outcome.ok, "detail": outcome.reason}
                except Exception as exc:
                    applied = {"approved": False, "detail": f"{type(exc).__name__}: {exc}"}
        else:
            applied = {"approved": True, "detail": "Recorded. No automatic action was required."}

        target.status = ApprovalStatus.APPROVED if applied["approved"] else ApprovalStatus.PENDING
        target.decided_at = now_ts()
        target.decided_by = owner_id
        target.decision_note = note or applied["detail"]
        self._store.save(company, owner_id)
        return company, {"ok": applied["approved"], **applied}

    def reject(self, company_id: str, owner_id: str, approval_id: str, *, note: str = "") -> tuple[Company, dict[str, Any]]:
        from alpha.company_os.models import ApprovalStatus

        company = self.require(company_id, owner_id)
        target = next((a for a in company.approvals if a.approval_id == approval_id), None)
        if target is None:
            return company, {"ok": False, "error": f"No approval '{approval_id}' on this company."}
        target.status = ApprovalStatus.REJECTED
        target.decided_at = now_ts()
        target.decided_by = owner_id
        target.decision_note = note
        self._store.save(company, owner_id)
        return company, {"ok": True}

    # -- reporting --------------------------------------------------------- #

    def metrics(self, company_id: str, owner_id: str) -> CompanyMetrics:
        """A measured snapshot. Every count is of rows that actually exist."""
        company = self.require(company_id, owner_id)
        work = portfolio.measure_work(company)
        if not work.get("reachable"):
            return CompanyMetrics(headcount=len(company.employments), measured_at=None)

        health = orchestrator.company_health(company, work)
        metrics = CompanyMetrics(
            headcount=len(company.employments),
            active_headcount=len(company.active_employments()),
            project_count=len(company.projects),
            active_project_count=sum(1 for p in company.projects if p.status == "active"),
            open_work_items=int(work.get("open_items") or 0),
            blocked_work_items=int(work.get("blocked_items") or 0),
            in_review_work_items=int(work.get("in_review_items") or 0),
            completed_work_items=int(work.get("done_items") or 0),
            room_count=len(company.groups),
            schedule_count=len(company.schedules),
            objective_count=len(company.objectives),
            pending_approvals=len(company.pending_approvals()),
            health_percent=health.get("health_percent"),
            health_basis=MeasurementBasis(health.get("basis", MeasurementBasis.UNMEASURED.value)),
            measured_at=now_ts(),
        )
        return metrics

    def digest(self, company_id: str, owner_id: str) -> dict[str, Any]:
        """The executive briefing payload for the Company tab.

        Assembled from measured subsystems only. A section whose read failed says
        so instead of rendering as zero.
        """
        company = self.require(company_id, owner_id)
        work = portfolio.measure_work(company)
        roster = workforce.sync_workforce_status(company)
        health = orchestrator.company_health(company, work)
        cost = loop_safety.cost_status(company)

        return {
            "company": company.model_dump(mode="json"),
            "metrics": self.metrics(company_id, owner_id).model_dump(mode="json"),
            "health": health,
            "work": work,
            "workforce": roster,
            "cost": cost,
            "loop": {
                "enabled": company.loop_policy.enabled,
                "state": company.state.value,
                "consecutive_no_progress_ticks": company.consecutive_no_progress_ticks,
                "recent_outcomes": list(company.recent_outcomes[-20:]),
                "last_ritual_at": dict(company.last_ritual_at),
                "policy": company.loop_policy.model_dump(mode="json"),
            },
            "rituals": orchestrator.run_rituals(company),
            "approvals": [a.model_dump(mode="json") for a in company.approvals[-20:]],
            "invariants_ok": not company.check_invariants(),
            "invariant_problems": company.check_invariants(),
        }

    def org_chart(self, company_id: str, owner_id: str) -> dict[str, Any]:
        """The reporting tree, built from real employments."""
        company = self.require(company_id, owner_id)
        nodes: list[dict[str, Any]] = []
        for employment in company.employments:
            role = company.role(employment.role_id)
            unit = company.unit(employment.unit_id)
            nodes.append(
                {
                    "handle": employment.agent_handle,
                    "employment_type": employment.employment_type.value,
                    "status": employment.status.value,
                    "reports_to": employment.reports_to_handle,
                    "unit_id": employment.unit_id,
                    "unit_name": unit.name if unit else None,
                    "role_title": role.title if role else None,
                    "clearance": role.clearance.value if role else None,
                    "decision_authority": list(role.decision_authority) if role else [],
                }
            )
        return {
            "nodes": nodes,
            "units": [u.model_dump(mode="json") for u in company.units],
            "accountabilities": [a.model_dump(mode="json") for a in company.accountabilities],
        }

    def rituals(self, company_id: str, owner_id: str) -> dict[str, Any]:
        company = self.require(company_id, owner_id)
        return orchestrator.run_rituals(company)


class TickRecordView:
    """A tick plus the company it produced, for a single response payload."""

    __slots__ = ("record", "company")

    def __init__(self, record: Any, company: Company):
        self.record = record
        self.company = company

    def as_dict(self) -> dict[str, Any]:
        return {"tick": self.record.model_dump(mode="json"), "company": self.company.model_dump(mode="json")}


_SERVICE: CompanyService | None = None


def get_company_service() -> CompanyService:
    """Process-wide service accessor."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = CompanyService()
    return _SERVICE


def reset_company_service() -> None:
    """Drop the cached service. Tests use this to isolate the runtime home."""
    global _SERVICE
    _SERVICE = None


__all__ = [
    "CompanyService",
    "CompanyNotFound",
    "get_company_service",
    "reset_company_service",
    "Charter",
]
