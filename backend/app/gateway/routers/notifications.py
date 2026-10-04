"""Notification API: the operator's durable notification history.

The notification store is process-wide and single-writer, like
every other store in this layer. These routes are the read and
preference surface; the *triggers* that create notifications live
in the harness (`alpha.notifications.triggers`) and fire from the
group subsystem's own mutation paths, so an event and its
notification cannot drift apart.

Route order matters: the literal `/preferences` is declared
before any parameterised sibling so Starlette matches it first.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from alpha.notifications import (
    NOTIFICATION_PRIORITIES,
    NOTIFICATION_TYPES,
    OPERATOR_USER_ID,
)
from alpha.notifications.triggers import NotificationTriggers, get_notification_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class PreferenceUpdate(BaseModel):
    enabled: bool | None = None
    sound_enabled: bool | None = None
    desktop_enabled: bool | None = None
    types: dict[str, bool] | None = None
    priorities: dict[str, bool] | None = None
    quiet_hours_start: str | None = Field(default=None, max_length=8)
    quiet_hours_end: str | None = Field(default=None, max_length=8)
    digest_mode: bool | None = None
    digest_interval_minutes: int | None = Field(default=None, ge=1, le=1440)


def _store():
    return get_notification_store()


def _triggers():
    return NotificationTriggers(_store())


def _current_user() -> str:
    """The operator identity notifications are filed under.

    In this single-process model the installation has one
    operator. A request-scoped identity would need the auth
    context, which these read-only routes deliberately do not
    require — the honest claim is "this installation's
    notifications", and mixing per-user histories without an
    authenticated identity would fabricate ownership.

    The id is `alpha.notifications.OPERATOR_USER_ID` rather than a
    literal, because the trigger path has to put the same identity on
    every audience. When the two sides each held their own copy of the
    string, room messages were filed under the agent names in a room's
    roster and this route served an inbox nothing had ever written to,
    so the bell was empty no matter how many notifications existed.
    """
    return OPERATOR_USER_ID


@router.get("", summary="Notification history")
async def list_notifications(
    unread_only: bool = Query(default=False),
    types: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """The operator's notification history, newest first.

    `types` is a comma-separated filter. A failed read is a
    raised error, never an empty list that reads as "nothing
    happened".
    """
    user_id = _current_user()
    wanted = [t.strip() for t in types.split(",") if t.strip()] if types else None

    def _read():
        return _store().list(
            user_id,
            unread_only=unread_only,
            types=wanted,
            limit=limit,
        )

    notifications = await asyncio.to_thread(_read)
    return {
        "notifications": [n.to_dict() for n in notifications],
        "count": len(notifications),
        "valid_types": list(NOTIFICATION_TYPES),
        "valid_priorities": list(NOTIFICATION_PRIORITIES),
    }


@router.get("/unread-count", summary="Unread notification count")
async def unread_count() -> dict:
    user_id = _current_user()

    def _read():
        return _store().unread_count(user_id)

    count = await asyncio.to_thread(_read)
    return {"user_id": user_id, "unread_count": count}


@router.post("/{notification_id}/read", summary="Mark a notification read")
async def mark_notification_read(notification_id: str) -> dict:
    user_id = _current_user()

    def _mark():
        return _store().mark_read(user_id, notification_id)

    found = await asyncio.to_thread(_mark)
    if not found:
        raise HTTPException(status_code=404, detail=f"Notification '{notification_id}' not found.")
    return {"notification_id": notification_id, "read": True}


@router.post("/read-all", summary="Mark every notification read")
async def mark_all_read() -> dict:
    user_id = _current_user()

    def _mark():
        return _store().mark_all_read(user_id)

    count = await asyncio.to_thread(_mark)
    return {"user_id": user_id, "marked_read": count}


# Declared before any parameterised sibling so Starlette matches
# the literal first.
@router.get("/preferences", summary="Notification preferences")
async def get_preferences() -> dict:
    user_id = _current_user()

    def _read():
        return _store().get_preference(user_id)

    pref = await asyncio.to_thread(_read)
    return {
        **pref.to_dict(),
        "valid_types": list(NOTIFICATION_TYPES),
        "valid_priorities": list(NOTIFICATION_PRIORITIES),
    }


@router.patch("/preferences", summary="Update notification preferences")
async def update_preferences(body: PreferenceUpdate) -> dict:
    user_id = _current_user()
    updates = body.model_dump(exclude_unset=True)

    def _update():
        return _store().update_preference(user_id, updates)

    pref = await asyncio.to_thread(_update)
    return pref.to_dict()


@router.get("/test", summary="Fire a test notification")
async def test_notification() -> dict:
    """Create one test notification so the operator can verify
    the delivery surface (sound, toast, panel) end to end.

    This is the only route that creates a notification on
    demand — every other creation path is a system trigger.
    """
    user_id = _current_user()

    def _create():
        from alpha.notifications import Notification, new_notification_id

        notification = Notification(
            notification_id=new_notification_id(),
            type="system",
            title="Alpha notification test",
            body="This is a test of the notification system.",
            priority="normal",
            action_url="/groups",
            icon="bell",
            sound=True,
        )
        object.__setattr__(notification, "user_id", user_id)
        return _store().create(notification)

    notification = await asyncio.to_thread(_create)
    return {"notification": notification.to_dict()}
