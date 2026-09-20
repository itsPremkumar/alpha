"""Real-time Bridge connecting Kanban Board events to Multi-Agent Group Chat."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from alpha.groups.service import get_group_chat_service

if TYPE_CHECKING:
    from alpha.kanban.models import KanbanTask

logger = logging.getLogger(__name__)


class KanbanGroupBridge:
    """Dispatches Kanban task progression notifications into Group Chat rooms."""

    @staticmethod
    def notify_task_event(
        room_id: str | None,
        event_type: str,
        task: KanbanTask,
        actor: str = "kanban_system",
        detail: str = "",
    ) -> None:
        if not room_id:
            return

        service = get_group_chat_service()

        icons = {
            "created": "📋 [TASK CREATED]",
            "claimed": "🏃 [TASK CLAIMED]",
            "review_requested": "🔍 [REVIEW REQUESTED]",
            "approved": "✅ [TASK APPROVED & DONE]",
            "unblocked": "🔓 [TASK AUTO-UNBLOCKED]",
            "changes_requested": "⚠️ [CHANGES REQUESTED]",
            "moved": "🔄 [TASK MOVED]",
        }
        header = icons.get(event_type, f"📌 [{event_type.upper()}]")

        content = (
            f"{header} **{task.task_id}**: {task.title}\n"
            f"- Column: `{task.column}` | Priority: `{task.priority}`\n"
            f"- Assignee: @{task.assignee or 'unassigned'} | Reviewer: @{task.reviewer or 'none'}"
        )
        if detail:
            content += f"\n- Detail: {detail}"

        try:
            service.post_message(
                room_name=room_id,
                sender=actor,
                content=content,
                intent="card_update",
                metadata={"task_id": task.task_id, "event": event_type},
            )
        except Exception as e:
            logger.warning("Failed to broadcast Kanban event to group %s: %s", room_id, e)
