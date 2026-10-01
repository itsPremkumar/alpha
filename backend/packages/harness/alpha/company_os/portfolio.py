"""Portfolio sync: project this company's work onto the real subsystems.

Every function here is a **projection**. It reads a real ``alpha.projects``
project, a real ``alpha.kanban`` board, a real ``alpha.groups`` room or a real
schedule, and folds what it finds back into the company's index. It never copies
the underlying record, and it never invents one.

The projection contract, applied uniformly:

* A subsystem that cannot be read yields ``status_basis=UNMEASURED`` and an
  ``unknown`` status. It does not yield ``todo``, and it does not yield an empty
  list that reads like "there is nothing".
* A subsystem record that does not exist yields an empty list *and* a
  ``reachable`` flag, so a caller can distinguish "no projects" from "the project
  store is unreachable".
"""

from __future__ import annotations

import logging
from typing import Any

from alpha.company_os.models import (
    Company,
    GroupLink,
    MeasurementBasis,
    ProjectLink,
    ScheduleLink,
    WorkItemLink,
    now_ts,
)

logger = logging.getLogger(__name__)

#: Kanban columns that count as open work. ``blocked`` is open but not runnable,
#: so it is tracked separately rather than lumped in with ready work.
OPEN_COLUMNS = ("backlog", "todo", "in_progress", "in_review", "blocked")
READY_COLUMNS = ("todo", "backlog")


def _company_board_id(company: Company) -> str:
    """The company's default board id.

    Derived from the company id so two companies never collide on one board,
    and prefixed so it is obvious in the board list that the board is
    company-owned rather than a user-created project board.
    """
    return f"company-{company.company_id}"


def sync_kanban(company: Company) -> dict[str, Any]:
    """Read the company's real Kanban board and project it onto work items.

    Returns a measurable summary. ``reachable=False`` means the board store could
    not be read at all, which the caller must show as a load failure rather than
    as an empty board.
    """
    board_id = _company_board_id(company)
    try:
        from alpha.kanban.store import get_kanban_store

        store = get_kanban_store()
        tasks = store.list_tasks(board_id)
    except Exception as exc:
        logger.warning("Kanban store unreachable for company %s: %s", company.company_id, exc)
        return {"reachable": False, "board_id": board_id, "error": f"{type(exc).__name__}: {exc}"}

    by_id: dict[tuple[str, str], WorkItemLink] = {(w.board_id, w.task_id): w for w in ensure_work_items_field(company)}
    for task in tasks:
        key = (board_id, task.task_id)
        link = by_id.get(key)
        if link is None:
            link = WorkItemLink(
                task_id=task.task_id,
                board_id=board_id,
                title=task.title,
                assignee_agent_handle=(task.assignee or "").strip().lower() or None,
                column=task.column,
                priority=task.priority,
            )
            company.work_items.append(link)
        else:
            link.title = task.title
            link.assignee_agent_handle = (task.assignee or "").strip().lower() or None
            link.column = task.column
            link.priority = task.priority

    counts = {col: 0 for col in OPEN_COLUMNS}
    for task in tasks:
        if task.column in counts:
            counts[task.column] += 1

    return {
        "reachable": True,
        "board_id": board_id,
        "total": len(tasks),
        "counts": counts,
        "open": sum(counts[c] for c in OPEN_COLUMNS),
        "ready": sum(counts[c] for c in READY_COLUMNS),
        "done": counts.get("done", 0),
        "measured": True,
    }


def ensure_work_items_field(company: Company) -> list[WorkItemLink]:
    """Return ``company.work_items``, grafting the field on an older record.

    A company persisted before work items existed must still load, so the field
    is grafted on rather than the document being rejected.
    """
    existing = getattr(company, "work_items", None)
    if existing is None:
        company.work_items = []
        return company.work_items
    return existing


def create_work_item(
    company: Company,
    *,
    title: str,
    description: str = "",
    priority: str = "normal",
    assignee_agent_handle: str | None = None,
    definition_of_done: list[str] | None = None,
    objective_id: str | None = None,
) -> WorkItemLink:
    """Create a real Kanban card on the company's board and index it."""
    board_id = _company_board_id(company)
    from alpha.kanban.store import get_kanban_store

    store = get_kanban_store()
    # ``alpha.kanban`` priorities are low/medium/high/critical; a caller may send
    # the coarser normal/urgent vocabulary, which is mapped rather than rejected
    # so a UI control and an API call can disagree about the word safely.
    mapped_priority = {"normal": "medium", "urgent": "high"}.get(priority.lower(), priority.lower())
    if mapped_priority not in ("low", "medium", "high", "critical"):
        mapped_priority = "medium"

    task = store.create_task(
        board_id=board_id,
        title=title,
        description=description,
        priority=mapped_priority,
        assignee=(assignee_agent_handle or "").strip().lower() or None,
    )
    link = WorkItemLink(
        task_id=task.task_id,
        board_id=board_id,
        title=title,
        assignee_agent_handle=(assignee_agent_handle or "").strip().lower() or None,
        column=task.column,
        priority=mapped_priority,
        objective_id=objective_id,
        definition_of_done=list(definition_of_done or []),
    )
    ensure_work_items_field(company).append(link)
    return link


def move_work_item(company: Company, task_id: str, column: str, *, board_id: str | None = None) -> dict[str, Any]:
    """Transition a real Kanban card and mirror the change onto the index.

    The board store is the authority. The company index is only updated with what
    the board actually reported, so the two cannot silently disagree.
    """
    target_board = board_id or _company_board_id(company)
    from alpha.kanban.store import get_kanban_store

    store = get_kanban_store()
    tasks = {t.task_id: t for t in store.list_tasks(target_board)}
    task = tasks.get(task_id)
    if task is None:
        return {"ok": False, "error": f"Card '{task_id}' is not on board '{target_board}'."}

    task.column = column  # type: ignore[assignment]
    from alpha.kanban.models import _now

    task.updated_at = _now()
    store._save()  # noqa: SLF001 - the store exposes no per-task update method

    for link in ensure_work_items_field(company):
        if link.task_id == task_id and link.board_id == target_board:
            link.column = task.column
    return {"ok": True, "task_id": task_id, "board_id": target_board, "column": task.column}


def index_projects(company: Company, rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Refresh the company's project index from rows supplied by the caller.

    **Projects are Gateway-owned.** ``get_project_repo`` lives in ``app.gateway.deps``
    and the harness must never import ``app.*`` (enforced by
    ``tests/test_harness_boundary.py``), so this module cannot enumerate projects
    itself. The Gateway reads the repo and passes rows in; the company OS indexes
    them. That is the correct direction: the harness owns organisation, the app
    layer owns tenancy-scoped repositories.

    Passing ``None`` means "the caller could not read the project store", which is
    reported as unreachable rather than as an empty portfolio.
    """
    if rows is None:
        return {"reachable": False, "error": "The project store was not readable by the caller."}

    known = {p.project_id for p in company.projects}
    added = 0
    for raw in rows:
        project_id = str(raw.get("project_id") or raw.get("id") or "").strip()
        if not project_id:
            continue
        existing = next((p for p in company.projects if p.project_id == project_id), None)
        if existing is None:
            existing = ProjectLink(project_id=project_id)
            company.projects.append(existing)
            known.add(project_id)
            added += 1
        existing.title = str(raw.get("name") or raw.get("title") or "") or existing.title
        existing.status = str(raw.get("status") or "unknown")
        existing.status_basis = MeasurementBasis.MEASURED
        existing.last_synced_at = now_ts()
        lead = str(raw.get("lead_agent_handle") or "").strip().lower()
        if lead:
            existing.lead_agent_handle = lead

    return {"reachable": True, "project_count": len(company.projects), "added": added, "measured": True}


def attach_project(company: Company, project_id: str, *, title: str = "", objective_ids: list[str] | None = None) -> ProjectLink:
    """Link an existing real project into the company's portfolio.

    The project is not created here: an operator attaching a project that already
    exists is the normal case, and creating a duplicate would be the exact
    duplication this package exists to prevent.
    """
    existing = next((p for p in company.projects if p.project_id == project_id), None)
    if existing is not None:
        if objective_ids:
            for oid in objective_ids:
                if oid not in existing.objective_ids:
                    existing.objective_ids.append(oid)
        if title:
            existing.title = title
        return existing

    link = ProjectLink(
        project_id=project_id,
        title=title,
        objective_ids=list(objective_ids or []),
        status="unknown",
        status_basis=MeasurementBasis.UNMEASURED,
        last_synced_at=now_ts(),
    )
    company.projects.append(link)
    return link


def _group_service():
    from alpha.groups.service import get_group_chat_service

    return get_group_chat_service()


def sync_groups(company: Company) -> dict[str, Any]:
    """Refresh the company's room index from ``alpha.groups``.

    Rooms live in the harness, so this read is direct. A failure is disclosed as
    unreachable rather than reported as "this company has no rooms".
    """
    try:
        rooms = _group_service().list_rooms()
    except Exception as exc:
        logger.info("Group service unavailable for company %s: %s", company.company_id, exc)
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}

    known = {g.room_id for g in company.groups}
    added = 0
    for room in rooms or []:
        room_id = str(getattr(room, "room_id", "") or "").strip()
        if not room_id or room_id in known:
            continue
        company.groups.append(
            GroupLink(
                room_id=room_id,
                name=str(getattr(room, "name", "") or ""),
                purpose=str(getattr(room, "topic", "") or ""),
                member_agent_handles=[str(m).strip().lower() for m in (getattr(room, "members", None) or [])],
            )
        )
        known.add(room_id)
        added += 1

    return {"reachable": True, "room_count": len(company.groups), "added": added, "measured": True}


def create_group(company: Company, *, name: str, topic: str = "", members: list[str] | None = None) -> GroupLink:
    """Create a real group room and index it.

    The room is created through the group service, which also auto-provisions any
    member bot that does not exist yet. The company's index then points at the
    room the service actually produced.
    """
    room = _group_service().get_or_create_room(
        name=name,
        topic=topic or "Company working room",
        members=[str(m).strip().lower() for m in (members or [])],
    )
    room_id = str(getattr(room, "room_id", "") or name.lower().strip())
    link = GroupLink(
        room_id=room_id,
        name=name,
        purpose=topic,
        member_agent_handles=[str(m).strip().lower() for m in (members or [])],
    )
    existing = next((g for g in company.groups if g.room_id == room_id), None)
    if existing is not None:
        existing.name = name
        existing.purpose = topic
        existing.member_agent_handles = list(link.member_agent_handles)
        return existing
    company.groups.append(link)
    return link


def post_group_message(company: Company, room_id: str, *, sender: str, content: str) -> dict[str, Any]:
    """Post to a real group room.

    The company posts *announcements* here. Routine liveness goes to the
    attendance ledger as a silent pulse instead, because a heartbeat that posts to
    a chat room trains everyone to ignore the channel.
    """
    try:
        message, _speakers = _group_service().post_message(room_name=room_id, sender=sender, content=content)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "room_id": room_id, "message": str(getattr(message, "content", content))}


def attach_schedule(
    company: Company,
    *,
    schedule_id: str,
    backend_task_id: str = "",
    backend: str = "scheduler",
    title: str = "",
    recurrence: str = "",
    owner_agent_handle: str | None = None,
) -> ScheduleLink:
    """Index an existing real schedule.

    The company never *fires* a schedule. ``alpha.scheduler`` owns ``when``; this
    records which company a schedule serves. Two owners for one occurrence is
    the failure this separation prevents.
    """
    existing = next((s for s in company.schedules if s.schedule_id == schedule_id), None)
    if existing is not None:
        if title:
            existing.title = title
        if recurrence:
            existing.recurrence = recurrence
        return existing
    link = ScheduleLink(
        schedule_id=schedule_id,
        backend=backend,  # type: ignore[arg-type]
        backend_task_id=backend_task_id or schedule_id,
        title=title,
        recurrence=recurrence,
        owner_agent_handle=(owner_agent_handle or "").strip().lower() or None,
    )
    company.schedules.append(link)
    return link


def measure_work(company: Company) -> dict[str, Any]:
    """A measured snapshot of the company's work, for the loop and the UI.

    Every number here is a count of rows that actually exist. Nothing is
    estimated, and ``reachable`` says whether the underlying read succeeded.
    """
    board = sync_kanban(company)
    if not board.get("reachable"):
        return {"reachable": False, "error": board.get("error", "Kanban store unreachable.")}
    items = ensure_work_items_field(company)
    return {
        "reachable": True,
        "measured": True,
        "total_items": len(items),
        "open_items": sum(1 for w in items if w.column in OPEN_COLUMNS),
        "ready_items": sum(1 for w in items if w.column in READY_COLUMNS),
        "blocked_items": sum(1 for w in items if w.column == "blocked"),
        "in_review_items": sum(1 for w in items if w.column == "in_review"),
        "done_items": sum(1 for w in items if w.column == "done"),
        "unassigned_items": sum(1 for w in items if w.column in READY_COLUMNS and not w.assignee_agent_handle),
        "items_missing_dod": sum(1 for w in items if w.column in READY_COLUMNS and not w.definition_of_done),
    }
