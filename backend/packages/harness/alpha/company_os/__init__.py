"""Company OS: durable, multi-tenant autonomous organizations over real subsystems.

This package is an **index and governance layer**, not a second agent runtime. It
owns the organisation — charter, objectives, org chart, employments,
accountabilities, budgets, approvals and the perpetual loop's own state — while
every entity it manages lives in the subsystem that already owns it:

============================  ==========================================
Concept                       Real owner
============================  ==========================================
Employee                      ``alpha.bots`` profile (``agent_handle``)
Project                       ``alpha.projects`` project (``project_id``)
Work item                     ``alpha.kanban`` card (``task_id``)
Group / meeting room          ``alpha.groups`` room (``room_id``)
Schedule                      ``alpha.scheduler`` / ``alpha.automations``
Tick ledger                   this package, append-only JSONL
============================  ==========================================

The one thing this package refuses to be is a place where numbers are invented.
A measurement that was not taken is ``None`` with a disclosed basis; a proposal
is never an action; a healthy idle tick says it spent nothing.

Public surface:

* :func:`get_company_service` — the owner-scoped facade the Gateway uses.
* :func:`get_company_store` — the durable store.
* :mod:`alpha.company_os.models` — every contract.
* :mod:`alpha.company_os.loop_safety` — the five breakers.
* :mod:`alpha.company_os.orchestrator` — the bounded tick.
"""

from __future__ import annotations

from alpha.company_os.blueprints import (
    Blueprint,
    PlannedEmployee,
    blueprint_for,
    list_archetypes,
    slugify_handle,
)
from alpha.company_os.models import (
    COMPANY_STORE_SCHEMA_VERSION,
    Accountability,
    ApprovalKind,
    ApprovalRequest,
    ApprovalStatus,
    AutonomyTier,
    Charter,
    ClearanceLevel,
    Company,
    CompanyMetrics,
    CompanyState,
    CostLedger,
    Employment,
    EmploymentStatus,
    EmploymentType,
    GroupLink,
    KeyResult,
    LoopPolicy,
    MeasurementBasis,
    Objective,
    ObjectiveStatus,
    OrgUnit,
    OrgUnitKind,
    ProjectLink,
    Role,
    ScheduleLink,
    TickOutcome,
    TickRecord,
    WorkItemLink,
)
from alpha.company_os.service import (
    CompanyService,
    get_company_service,
    reset_company_service,
)
from alpha.company_os.store import (
    CompanyNotFound,
    CompanyStore,
    CompanyStoreError,
    CompanyStoreUnreadable,
    get_company_store,
    reset_company_store,
)

__all__ = [
    # facade + store
    "CompanyService",
    "get_company_service",
    "reset_company_service",
    "CompanyStore",
    "CompanyStoreError",
    "CompanyStoreUnreadable",
    "CompanyNotFound",
    "get_company_store",
    "reset_company_store",
    # blueprints
    "Blueprint",
    "PlannedEmployee",
    "blueprint_for",
    "list_archetypes",
    "slugify_handle",
    # contracts
    "COMPANY_STORE_SCHEMA_VERSION",
    "Company",
    "CompanyMetrics",
    "CompanyState",
    "Charter",
    "Role",
    "OrgUnit",
    "OrgUnitKind",
    "ClearanceLevel",
    "AutonomyTier",
    "Employment",
    "EmploymentType",
    "EmploymentStatus",
    "Accountability",
    "Objective",
    "ObjectiveStatus",
    "KeyResult",
    "MeasurementBasis",
    "LoopPolicy",
    "CostLedger",
    "ProjectLink",
    "WorkItemLink",
    "GroupLink",
    "ScheduleLink",
    "TickRecord",
    "TickOutcome",
    "ApprovalRequest",
    "ApprovalKind",
    "ApprovalStatus",
]
