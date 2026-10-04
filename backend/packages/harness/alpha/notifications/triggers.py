"""Notification triggers: turning group events into durable records.

This is the *only* path that writes notifications, so the
"a notification exists" claim and the "an event happened" claim
cannot drift apart. Each trigger decides whether an event is
notification-worthy and, if so, creates exactly one record.

Trigger rules:

- **Mentions are always high priority.** A direct @mention is a
  summons; it outranks ordinary message flow.
- **Crashes and blockers are urgent.** A dead agent or a blocked
  path is the operator's problem now.
- **Ordinary messages are normal** and only fire when the user is
  not the sender — your own post does not notify you.
- **A message is soundable; the preference is the mute.** Every room
  message a recipient is not the sender of may play a sound. The
  operator's `sound_enabled` flag and quiet hours decide delivery,
  because they gate the surface without deleting the record. The
  record itself carries no hard mute: a second, undeclared silence
  switch is how an agent post ends up badged in the panel but
  inaudible, which is the one outcome the sound setting cannot fix.
- **The audience must contain whoever will read the inbox.** A
  notification filed under an agent name is unreadable, so whoever
  raises one is responsible for putting the operator on the list
  (`OPERATOR_USER_ID`).
- **Runs are high on start, normal on completion.** The operator
  cares that work began; a finished run is informational.
- **Every decision consults the preference first.** A disabled
  preference means the event happened but nobody was told — which
  is the correct outcome when the operator asked for quiet.
"""

from __future__ import annotations

import logging
from typing import Any

from alpha.notifications import (
    Notification,
    NotificationStore,
    new_notification_id,
)

logger = logging.getLogger(__name__)


class NotificationTriggers:
    """Create notifications from group subsystem events.

    The store is shared process-wide; the triggers are stateless
    decision functions. Keeping them separate means a caller that
    wants to record an event without a notification (a background
    reconciliation, a test) can call the store directly.
    """

    def __init__(self, store: NotificationStore | None = None):
        self.store = store or get_notification_store()

    # ── Message events ───────────────────────────────────────────

    def on_message_posted(
        self,
        room_id: str,
        room_name: str,
        sender: str,
        content: str,
        intent: str,
        mentions: list[str],
        message_id: str,
        recipients: list[str] | None = None,
    ) -> list[Notification]:
        """Notify room members that a message landed.

        The sender is excluded — posting your own message back to
        yourself as a notification is noise, not signal. Mentions
        inside the content are handled by :meth:`on_mention` so a
        single post can raise both a room notification and per-user
        mention notifications.
        """
        created: list[Notification] = []
        audience = list(recipients or [])
        for user in audience:
            if user == sender:
                continue
            if not self._allows(user, "message", "normal"):
                continue
            created.append(
                self._create(
                    user,
                    type="message",
                    title=f"New message in {room_name}",
                    body=_preview(content),
                    room_id=room_id,
                    message_id=message_id,
                    sender=sender,
                    priority="normal",
                    action_url=f"/groups/{room_name}",
                    icon="message",
                    # An agent's message is the thing the operator asked to be
                    # told about, so the record is soundable. This flag used to
                    # be `intent in _LOUD_INTENTS`, which muted the *default*
                    # intent -- so ordinary agent traffic badged the bell and
                    # stayed silent, and the only intents that rang were the
                    # ones already carrying a raised priority. A per-record
                    # hard mute is also a second, undeclared mute: the
                    # operator's `sound_enabled` preference and quiet hours are
                    # the documented controls, and they decide delivery without
                    # deleting the record.
                    sound=True,
                )
            )
        return created

    def on_mention(
        self,
        room_id: str,
        room_name: str,
        sender: str,
        content: str,
        mentioned: list[str],
        message_id: str,
    ) -> list[Notification]:
        """Notify each mentioned user. High priority, always.

        A mention is a direct address, so it is `high` rather than
        `normal`: it should outrank ordinary room chatter in the
        panel and the digest.
        """
        created: list[Notification] = []
        for user in mentioned:
            if user == sender:
                continue
            if not self._allows(user, "mention", "high"):
                continue
            created.append(
                self._create(
                    user,
                    type="mention",
                    title=f"{sender} mentioned you in {room_name}",
                    body=_preview(content),
                    room_id=room_id,
                    message_id=message_id,
                    sender=sender,
                    priority="high",
                    action_url=f"/groups/{room_name}/messages/{message_id}",
                    icon="at-sign",
                    sound=True,
                )
            )
        return created

    # ── Activity events ───────────────────────────────────────────

    def on_activity_change(
        self,
        room_id: str,
        room_name: str,
        bot_name: str,
        new_state: str,
        evidence: dict[str, Any] | None = None,
    ) -> list[Notification]:
        """Notify on states that need operator attention.

        Only `crashed`, `blocked` and `unresponsive` raise a
        notification — `idle` and `working` are the normal rhythm
        of a room and notifying on them would be alarm fatigue.
        A crash is `urgent` (sound, interaction required); the
        other two are `high`.
        """
        if new_state not in _ATTENTION_STATES:
            return []
        priority = "urgent" if new_state == "crashed" else "high"
        reason = (evidence or {}).get("reason", new_state)
        title = _activity_title(new_state, bot_name)
        return (
            [
                self._create(
                    "operator",
                    type="activity",
                    title=title,
                    body=reason,
                    room_id=room_id,
                    sender=bot_name,
                    priority=priority,
                    action_url=f"/groups/{room_name}/activity",
                    icon="alert",
                    sound=True,
                )
            ]
            if self._allows("operator", "activity", priority)
            else []
        )

    # ── Claim events ───────────────────────────────────────────

    def on_claim_created(
        self,
        room_id: str,
        room_name: str,
        claim: dict[str, Any],
    ) -> list[Notification]:
        """Notify the room that work was claimed.

        Claims are advisory, so the notification is `normal` —
        informative, not urgent. A soft conflict on the same
        subject is the interesting case and is surfaced by the
        coordination layer itself.
        """
        if not self._allows("operator", "claim", "normal"):
            return []
        return [
            self._create(
                "operator",
                type="claim",
                title=f"New work claim in {room_name}",
                body=f"{claim.get('claimant', claim.get('holder', 'someone'))} claimed: {claim.get('subject', '')}",
                room_id=room_id,
                sender=claim.get("claimant", claim.get("holder")),
                priority="normal",
                action_url=f"/groups/{room_name}/claims",
                icon="clipboard",
                sound=False,
            )
        ]

    # ── Run events ───────────────────────────────────────────

    def on_run_started(
        self,
        room_id: str,
        room_name: str,
        run: dict[str, Any],
    ) -> list[Notification]:
        if not self._allows("operator", "run", "high"):
            return []
        return [
            self._create(
                "operator",
                type="run",
                title=f"Team run started in {room_name}",
                body=str(run.get("objective", "")[:200]),
                room_id=room_id,
                priority="high",
                action_url=f"/groups/{room_name}/runs/{run.get('run_id', '')}",
                icon="play",
                sound=True,
            )
        ]

    def on_run_completed(
        self,
        room_id: str,
        room_name: str,
        run: dict[str, Any],
    ) -> list[Notification]:
        if not self._allows("operator", "run", "normal"):
            return []
        return [
            self._create(
                "operator",
                type="run",
                title=f"Team run completed in {room_name}",
                body=f"Status: {run.get('status', 'unknown')}",
                room_id=room_id,
                priority="normal",
                action_url=f"/groups/{room_name}/runs/{run.get('run_id', '')}",
                icon="check",
                sound=False,
            )
        ]

    # ── Internals ───────────────────────────────────────────

    def _create(
        self,
        user_id: str,
        *,
        type: str,
        title: str,
        body: str,
        room_id: str | None = None,
        message_id: str | None = None,
        sender: str | None = None,
        priority: str = "normal",
        action_url: str | None = None,
        icon: str | None = None,
        sound: bool = True,
    ) -> Notification:
        notification = Notification(
            notification_id=new_notification_id(),
            type=type,
            title=title,
            body=body[:500],
            room_id=room_id,
            message_id=message_id,
            sender=sender,
            priority=priority,
            action_url=action_url,
            icon=icon,
            sound=sound,
        )
        # Stash the owner so the store files it under the right user.
        object.__setattr__(notification, "user_id", user_id)
        return self.store.create(notification)

    def _allows(self, user_id: str, ntype: str, priority: str) -> bool:
        try:
            return self.store.get_preference(user_id).allows(ntype, priority)
        except Exception:
            # A preference that cannot be read defaults to allowing
            # the notification: failing open here means the operator
            # still sees a crash, which is the safer error.
            return True


#: The activity states that raise a notification. `idle`, `working`
#: and `offline` are the room's normal rhythm and do not notify.
_ATTENTION_STATES = frozenset({"crashed", "blocked", "unresponsive"})


def _preview(content: str, limit: int = 200) -> str:
    """A bounded excerpt for a notification body.

    The body is what the operator reads before opening the room,
    so it is capped rather than truncated mid-word — a whole word
    boundary keeps the preview honest without a dangling suffix.
    """
    text = (content or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _activity_title(state: str, bot_name: str) -> str:
    return {
        "crashed": f"{bot_name} crashed",
        "blocked": f"{bot_name} is blocked",
        "unresponsive": f"{bot_name} is unresponsive",
    }.get(state, f"{bot_name}: {state}")


_store: NotificationStore | None = None


def get_notification_store() -> NotificationStore:
    """The process-wide notification store."""
    global _store
    if _store is None:
        _store = NotificationStore()
    return _store
