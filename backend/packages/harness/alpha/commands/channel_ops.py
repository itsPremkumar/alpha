"""The runtime entry point for war-room, addressing and lifecycle operations.

This module is what makes the work REACHABLE. Everything in
:mod:`alpha.groups.war_room`, :mod:`alpha.groups.lifecycle_ops`,
:mod:`alpha.groups.acquisition`, :mod:`alpha.groups.supervisor` and
:mod:`alpha.channels` is library code until something calls it from a real
path. This module is that something:

* :func:`open_war_room` is called by the war-room tool
  (:mod:`alpha.tools.builtins.war_room_tool`) on every agent, and by the
  in-channel slash command ``/war-room``;
* :func:`handle_channel_message` is the single door for a chat message: it
  sequences the message, resolves mentions once, dispatches per target, and
  handles a leading slash command;
* :func:`run_lifecycle_command` backs ``/hire``, ``/clone``, ``/rescope`` and
  ``/archive``.

It is deliberately thin. Its job is wiring and defaults, not policy: the policy
lives in the components, so there is exactly one of each rule.

The in-channel command table below is the SAME table
:class:`alpha.channels.routing.ChannelCommandRouter` permission-checks against,
so a command's permissions are the tool permissions it already has.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.bots.events import OrgEventStore
from alpha.channels import ledger as channel_ledger
from alpha.channels.mentions import MentionResolution, parse_mentions
from alpha.channels.routing import (
    ChannelCommandRouter,
    CommandRequest,
    DispatchReport,
    dispatch_resolution,
    parse_command,
)
from alpha.channels.transcript import TranscriptStore
from alpha.groups.lifecycle_ops import BotLifecycle
from alpha.groups.supervisor import Supervisor

logger = logging.getLogger(__name__)

#: The in-channel command table: command -> (tool name for the permission gate,
#: whether the command's blast radius includes a bot other than the actor).
#:
#: ``mutates_others=True`` marks the commands that may be issued on another
#: bot's behalf, and those get the extra target-authority check.
CHANNEL_COMMANDS: dict[str, tuple[str, bool]] = {
    "war-room": ("deliberate", False),
    "hire": ("hire_bot", True),
    "clone": ("rescope_bot", True),
    "rescope": ("rescope_bot", True),
    "archive": ("rescope_bot", True),
    "acquire": ("skill_manage", True),
    "status": ("view_file", False),
}

#: Where war-room runs, transcripts and acquired artefacts live.
DEFAULT_RUNTIME_ROOT = "war_room"


def runtime_root(base: str | Path | None = None) -> Path:
    """Resolve the runtime root, honouring ``AGENT_WORKSPACE_HOME``."""
    import os

    if base is not None:
        return Path(base)
    home = os.getenv("AGENT_WORKSPACE_HOME", "").strip()
    if home:
        return Path(home) / DEFAULT_RUNTIME_ROOT
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / DEFAULT_RUNTIME_ROOT
    except Exception:
        return Path.cwd() / ".alpha" / DEFAULT_RUNTIME_ROOT


@dataclass(slots=True)
class ChannelContext:
    """Everything one room needs, assembled once per channel."""

    room: str
    root: Path
    event_store: OrgEventStore
    transcript: TranscriptStore
    commands: ChannelCommandRouter
    lifecycle: BotLifecycle | None = None
    supervisor: Supervisor | None = None

    @property
    def ledger_store(self) -> OrgEventStore:
        return self.event_store


def open_channel(
    room: str,
    *,
    root: str | Path | None = None,
    roster: Sequence[str] = (),
    roles: Mapping[str, Sequence[str]] | None = None,
    lifecycle: BotLifecycle | None = None,
    supervisor: Supervisor | None = None,
) -> ChannelContext:
    """Assemble a channel's runtime context: one transcript, one ledger, one router."""
    base = runtime_root(root)
    room_dir = base / "rooms" / room
    room_dir.mkdir(parents=True, exist_ok=True)
    store = OrgEventStore(base / "events.jsonl")
    return ChannelContext(
        room=room,
        root=base,
        event_store=store,
        transcript=TranscriptStore(room_dir / "transcript.jsonl"),
        commands=ChannelCommandRouter(
            CHANNEL_COMMANDS,
            target_authorities={
                name: frozenset(getattr(profile, "granted_capabilities", ()) or ())
                for name, profile in _profiles(lifecycle).items()
            },
        ),
        lifecycle=lifecycle,
        supervisor=supervisor,
    )


def _profiles(lifecycle: BotLifecycle | None) -> Mapping[str, Any]:
    if lifecycle is None:
        return {}
    try:
        return {p.name: p for p in lifecycle.store.list_profiles()}
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# the single door for a chat message
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ChannelMessageResult:
    """What happened to one message. Never hides a partial failure."""

    seq: int
    resolution: dict[str, Any]
    dispatch: dict[str, Any] | None
    command: dict[str, Any] | None
    human_line: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "resolution": self.resolution,
            "dispatch": self.dispatch,
            "command": self.command,
            "human_line": self.human_line,
        }


async def handle_channel_message(
    ctx: ChannelContext,
    author: str,
    body: str,
    *,
    target_hint: str | None = None,
    command_handler: Callable[[CommandRequest], Awaitable[Any] | Any] | None = None,
    target_handler: Callable[[str, str, MentionResolution], Awaitable[Any] | Any] | None = None,
    actor_role: str = "lead",
    on_behalf_of: str | None = None,
) -> ChannelMessageResult:
    """Sequence, resolve once, then either run a command or dispatch.

    A leading ``/command`` is handled as a command and NOT also dispatched as
    chat, so a command cannot be smuggled past the permission check by being
    addressed to a bot. Everything else goes through the mention pipeline.
    """
    message = ctx.transcript.append(
        author,
        body,
        kind="chat",
        targets=[target_hint] if target_hint else (),
    )
    channel_ledger.note_message(
        author,
        target=ctx.room,
        body_chars=len(body or ""),
        seq=message.seq,
        store=ctx.event_store,
    )

    parsed = parse_command(body)
    if parsed is not None:
        name, args = parsed
        request = CommandRequest(
            name=name,
            args=args,
            actor=author,
            actor_role=actor_role,
            body=body,
            room=ctx.room,
            on_behalf_of=on_behalf_of,
        )
        outcome = await ctx.commands.dispatch(request, command_handler)
        return ChannelMessageResult(
            seq=message.seq,
            resolution={"command": name, "args": args},
            dispatch=None,
            command=outcome.to_dict(),
            human_line=(
                f"/{name} {'allowed' if outcome.decision.allowed else 'REFUSED'}: {outcome.decision.reason}"
            ),
        )

    roster = [name for name in _profiles(ctx.lifecycle)] or [target_hint or author]
    resolution = parse_mentions(
        body,
        roster=roster,
        roles={},
        sender=author,
    )
    if target_handler is None:
        return ChannelMessageResult(
            seq=message.seq,
            resolution=resolution.to_dict(),
            dispatch=None,
            command=None,
            human_line=resolution.human_line(),
        )
    report: DispatchReport = await dispatch_resolution(
        resolution,
        target_handler,
        sender=author,
        room=ctx.room,
        ledger_store=ctx.event_store,
    )
    return ChannelMessageResult(
        seq=message.seq,
        resolution=resolution.to_dict(),
        dispatch=report.to_dict(),
        command=None,
        human_line=report.human_line(),
    )
