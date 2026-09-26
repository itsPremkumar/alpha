"""Addressing execution: multi-target dispatch and in-channel slash commands.

Two responsibilities, both built on the previous two modules.

**Multi-target dispatch.** A mention resolution can name one bot, several bots,
or a role selector. All of them are dispatched CONCURRENTLY, and each target is
ISOLATED: one target raising is recorded in that target's own outcome and cannot
cancel, delay or degrade a sibling. This is the same invariant the group runner
enforces for deliberation members, applied to the addressing path.

**Slash commands inside a channel.** A command is not special because it is the
first thing in a message. This router extracts a leading ``/command`` from a
channel message body, resolves it against the command table, and permission
checks it **per actor** using the existing ``bots.permissions`` gate. The rule
that matters: a bot permitted to issue a command to ITSELF is not thereby
permitted to issue it against ANOTHER bot. On-behalf-of actions are checked
twice, once for the acting role and once for the target's own authority, and
the second check is what makes the asymmetry real.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.bots.authority_ceiling import AuthorityViolation, is_protected_component
from alpha.bots.permissions import ToolPermissionGate, get_permission_gate
from alpha.channels import ledger
from alpha.channels.mentions import MentionResolution, normalise_handle

logger = logging.getLogger(__name__)

#: Status values for one dispatched target.
TARGET_OK = "ok"
TARGET_FAILED = "failed"
TARGET_REFUSED = "refused"
TARGET_SKIPPED = "skipped"

#: A slash command is the first token of a body and must be a bare word.
_COMMAND_RE = re.compile(r"^/(?P<name>[a-z][a-z0-9_.\-]{0,63})(?P<rest>\s.*)?$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# multi-target dispatch
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class TargetOutcome:
    """One target's own result. Never merged, never inferred from a sibling."""

    target: str
    status: str
    via: str = ""
    output: str = ""
    error: str = ""
    error_type: str = ""
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == TARGET_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "status": self.status,
            "via": self.via,
            "output": self.output,
            "error": self.error,
            "error_type": self.error_type,
            "duration_ms": self.duration_ms,
        }


@dataclass(slots=True)
class DispatchReport:
    """The whole addressing result: per-target outcomes plus what did not resolve."""

    outcomes: list[TargetOutcome] = field(default_factory=list)
    unresolved: list[dict[str, str]] = field(default_factory=list)
    resolution_line: str = ""

    @property
    def ok(self) -> bool:
        """True only when every resolved target succeeded."""
        return not self.unresolved and all(o.ok for o in self.outcomes)

    @property
    def delivered(self) -> tuple[str, ...]:
        return tuple(o.target for o in self.outcomes if o.ok)

    @property
    def degraded(self) -> tuple[str, ...]:
        """Targets that did not deliver. A non-empty value here is never hidden."""
        return tuple(o.target for o in self.outcomes if not o.ok)

    def outcome_for(self, target: str) -> TargetOutcome | None:
        key = normalise_handle(target)
        for outcome in self.outcomes:
            if normalise_handle(outcome.target) == key:
                return outcome
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "resolution": self.resolution_line,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "unresolved": [dict(u) for u in self.unresolved],
            "delivered": list(self.delivered),
            "degraded": list(self.degraded),
        }

    def human_line(self) -> str:
        bits = [f"@{o.target}={o.status}" + (f"({o.error_type})" if o.error_type else "") for o in self.outcomes]
        bad = [f"{u['raw']}=UNRESOLVED" for u in self.unresolved]
        return "; ".join(bits + bad) or "no targets"

#: A target handler. Sync or async; both are supported.
TargetHandler = Callable[[str, str, MentionResolution], Awaitable[Any] | Any]


def _via_for(resolution: MentionResolution, target: str) -> str:
    for mention in resolution.targets:
        if target in mention.resolved:
            return mention.via
    return "unknown"


async def dispatch_resolution(
    resolution: MentionResolution,
    handler: TargetHandler,
    *,
    sender: str = "user",
    room: str = "general",
    ledger_store: Any | None = None,
    record_ledger: bool = True,
) -> DispatchReport:
    """Dispatch one message to every resolved target, concurrently and isolated.

    ``handler(target, body, resolution)`` is awaited (or called) per target
    inside its own ``try``/``except``. A target that raises produces a
    :class:`TargetOutcome` with ``status="failed"``; it cannot propagate out,
    cancel a sibling, or shorten another target's work.

    Unresolved mentions are reported, never dispatched. There is no fallback
    target: a typo addresses nobody.
    """
    report = DispatchReport(
        unresolved=[dict(u) for u in resolution.unresolved],
        resolution_line=resolution.human_line(),
    )
    loop = asyncio.get_running_loop()
    ordered: list[tuple[str, str]] = []
    for mention in resolution.targets:
        for handle in mention.resolved:
            ordered.append((handle, mention.via))

    async def _one(target: str, via: str) -> TargetOutcome:
        started = loop.time()
        try:
            result = handler(target, resolution.text, resolution)
            if inspect.isawaitable(result):
                result = await result
        except asyncio.CancelledError:
            # The ROOM was cancelled, not this target. Still recorded per-target
            # so the transcript shows what happened to this addressee.
            return TargetOutcome(
                target=target,
                status=TARGET_FAILED,
                via=via,
                error_type="cancelled",
                error="target cancelled with the room",
                duration_ms=int((loop.time() - started) * 1000),
            )
        except BaseException as exc:  # noqa: BLE001 - isolation is the point
            logger.warning("dispatch to @%s failed: %s: %s", target, type(exc).__name__, exc)
            return TargetOutcome(
                target=target,
                status=TARGET_FAILED,
                via=via,
                error=f"{type(exc).__name__}: {exc}",
                error_type=type(exc).__name__,
                duration_ms=int((loop.time() - started) * 1000),
            )
        return TargetOutcome(
            target=target,
            status=TARGET_OK,
            via=via,
            output="" if result is None else str(result),
            duration_ms=int((loop.time() - started) * 1000),
        )

    # return_exceptions=True is belt and braces: _one already swallows, so a
    # sibling can never be cancelled by gather's default behaviour.
    gathered = await asyncio.gather(
        *(_one(t, v) for t, v in ordered), return_exceptions=True
    )
    for (target, via), outcome in zip(ordered, gathered, strict=True):
        if isinstance(outcome, BaseException):
            report.outcomes.append(
                TargetOutcome(
                    target=target,
                    status=TARGET_FAILED,
                    via=via,
                    error=f"{type(outcome).__name__}: {outcome}",
                    error_type=type(outcome).__name__,
                )
            )
        else:
            report.outcomes.append(outcome)

    if record_ledger:
        try:
            ledger.note_mention(
                sender,
                target=room,
                resolution_line=report.resolution_line,
                ok=resolution.ok,
                delivered=list(report.delivered),
                degraded=list(report.degraded),
                store=ledger_store,
            )
        except ledger.LedgerUnavailable:
            # Fail closed: an unrecorded addressing decision is not a decision.
            raise
    return report


# ---------------------------------------------------------------------------
# in-channel slash commands
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class CommandRequest:
    name: str
    args: str
    actor: str
    actor_role: str
    body: str
    room: str
    #: Bot the command acts upon. ``None`` means the actor itself.
    on_behalf_of: str | None = None

    @property
    def targets_other(self) -> bool:
        return bool(self.on_behalf_of) and normalise_handle(self.on_behalf_of or "") != normalise_handle(self.actor)


@dataclass(slots=True)
class CommandDecision:
    allowed: bool
    reason: str
    needs_human_approval: bool = False
    #: Which check refused: "permission", "target_authority", "protected", "unknown".
    refused_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "needs_human_approval": self.needs_human_approval,
            "refused_by": self.refused_by,
        }


@dataclass(slots=True)
class CommandOutcome:
    request: CommandRequest
    decision: CommandDecision
    result: Any = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.decision.allowed and not self.error

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.request.name,
            "actor": self.request.actor,
            "actor_role": self.request.actor_role,
            "on_behalf_of": self.request.on_behalf_of,
            "room": self.request.room,
            "decision": self.decision.to_dict(),
            "result": self.result,
            "error": self.error,
            "ok": self.ok,
        }


def parse_command(body: str) -> tuple[str, str] | None:
    """Split a leading ``/command args`` off a channel message body.

    Returns ``None`` when the body is not a command, so an ordinary message
    beginning with a slash-free sentence is never mistaken for one.
    """
    match = _COMMAND_RE.match((body or "").strip())
    if not match:
        return None
    return match.group("name").lower(), (match.group("rest") or "").strip()


class ChannelCommandRouter:
    """In-channel slash commands, permission-checked per actor.

    The tool surface is declared per command as ``(tool_name, mutates_others)``.
    ``tool_name`` is the name handed to the existing
    :meth:`ToolPermissionGate.check_permission`, so command permissions are the
    same permissions every other tool already has, not a second policy.

    ``mutates_others`` marks commands whose blast radius includes a bot other
    than the actor. Those get the extra target-authority check described in the
    module docstring.
    """

    def __init__(
        self,
        commands: Mapping[str, tuple[str, bool]] | None = None,
        *,
        gate: ToolPermissionGate | None = None,
        target_authorities: Mapping[str, frozenset[str] | set[str]] | None = None,
    ) -> None:
        self.commands: dict[str, tuple[str, bool]] = dict(commands or {})
        self.gate = gate or get_permission_gate()
        self.target_authorities: dict[str, frozenset[str]] = {
            normalise_handle(k): frozenset(normalise_handle(c) for c in v)
            for k, v in (target_authorities or {}).items()
        }

    def set_target_authority(self, handle: str, capabilities: Sequence[str]) -> None:
        self.target_authorities[normalise_handle(handle)] = frozenset(
            normalise_handle(c) for c in capabilities
        )

    def check(self, request: CommandRequest) -> CommandDecision:
        """Permission-check one command request. Never raises."""
        entry = self.commands.get(request.name)
        if entry is None:
            return CommandDecision(
                allowed=False,
                reason=f"unknown command /{request.name}",
                refused_by="unknown",
            )
        tool_name, mutates_others = entry

        allowed, reason, needs_approval = self.gate.check_permission(
            request.actor_role, tool_name
        )
        if needs_approval:
            return CommandDecision(
                allowed=False,
                reason=reason or f"tool {tool_name!r} requires human approval",
                needs_human_approval=True,
                refused_by="permission",
            )
        if not allowed:
            return CommandDecision(
                allowed=False,
                reason=reason or f"role {request.actor_role!r} may not use {tool_name!r}",
                refused_by="permission",
            )

        if not request.targets_other:
            # Self-issued: the role ring is the whole answer.
            return CommandDecision(allowed=True, reason=f"role {request.actor_role!r} may use {tool_name!r} on itself")

        if is_protected_component(request.on_behalf_of or ""):
            return CommandDecision(
                allowed=False,
                reason=(
                    f"@{request.on_behalf_of} is a protected enforcement component; no actor may "
                    f"modify it on another actor's behalf"
                ),
                refused_by="protected",
            )

        if not mutates_others:
            return CommandDecision(
                allowed=False,
                reason=(
                    f"/{request.name} is a self-scoped command; @{request.actor} may not issue it "
                    f"against @{request.on_behalf_of}"
                ),
                refused_by="target_authority",
            )

        target = self.target_authorities.get(normalise_handle(request.on_behalf_of or ""))
        if not target:
            return CommandDecision(
                allowed=False,
                reason=(
                    f"no recorded authority for @{request.on_behalf_of}; an on-behalf-of action "
                    f"against an unknown bot is refused rather than assumed"
                ),
                refused_by="target_authority",
            )
        needed = self.commands[request.name][0]
        if needed and needed not in {t for t in target}:
            # The target does not hold the capability the command needs. The
            # actor's own permission is not a licence to act on someone else.
            return CommandDecision(
                allowed=False,
                reason=(
                    f"@{request.on_behalf_of} does not hold {needed!r}; @{request.actor} may issue "
                    f"/{request.name} to itself but not on its behalf"
                ),
                refused_by="target_authority",
            )
        return CommandDecision(
            allowed=True,
            reason=(
                f"role {request.actor_role!r} may use {tool_name!r}, and @{request.on_behalf_of} "
                f"independently holds {needed!r}"
            ),
        )

    async def dispatch(
        self,
        request: CommandRequest,
        handler: Callable[[CommandRequest], Awaitable[Any] | Any] | None = None,
        *,
        record_ledger: bool = True,
    ) -> CommandOutcome:
        """Check then, only if allowed, run. A refusal never calls the handler."""
        decision = self.check(request)
        if not decision.allowed:
            if record_ledger:
                try:
                    ledger.append(
                        ledger.EV_DISPATCH_REFUSED,
                        actor=request.actor,
                        target=request.on_behalf_of or request.room,
                        reason=f"/{request.name} refused ({decision.refused_by}): {decision.reason}",
                        details={"command": request.name, "refused_by": decision.refused_by},
                    )
                except (ledger.LedgerUnavailable, ledger.LedgerRefused):
                    logger.error("ledger unavailable while recording a command refusal")
                    raise
            return CommandOutcome(request=request, decision=decision)

        if handler is None:
            return CommandOutcome(request=request, decision=decision, result=None)
        try:
            value = handler(request)
            if inspect.isawaitable(value):
                value = await value
        except AuthorityViolation as exc:
            return CommandOutcome(
                request=request,
                decision=decision,
                error=str(exc),
            )
        except BaseException as exc:  # noqa: BLE001
            logger.warning("/%s failed for @%s: %s", request.name, request.actor, exc)
            return CommandOutcome(request=request, decision=decision, error=f"{type(exc).__name__}: {exc}")

        if record_ledger:
            try:
                ledger.note_dispatch(
                    request.actor,
                    target=request.on_behalf_of or request.room,
                    objective=f"/{request.name} {request.args}"[:2000],
                    reason=decision.reason,
                    command=request.name,
                )
            except (ledger.LedgerUnavailable, ledger.LedgerRefused):
                raise
        return CommandOutcome(request=request, decision=decision, result=value)
