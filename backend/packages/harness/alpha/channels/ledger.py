"""Fail-closed bridge onto the ONE governance ledger.

There is exactly one ledger in this codebase:
:mod:`alpha.bots.governance_ledger` over :mod:`alpha.bots.events`. Both write to
the same append-only JSONL with the same monotonic ``seq``, so authority actions
and ordinary operational events interleave in a single ordered, gap-free
record. This module is a bridge, not a second log.

Why the bridge exists
---------------------
``OrgEventStore.append_event`` swallows a failed disk write, logs a warning and
**returns the event as if it had been written** (``bots/events.py:119-126``).
``query_events`` then serves it from a 250-entry in-memory deque, so the entry
looks fine right up until a restart, when it is gone. For an authority action
that is unacceptable: "we did it and forgot to record it" is exactly the class
of failure an audit exists to catch.

So this bridge adds the one thing the store lacks: a **durability read-back**.
After every append it proves the entry is physically on disk, and if it is not
it raises :class:`LedgerUnavailable`. Callers are expected to treat that as
"the operation did not happen", not as "log and continue". That is the
fail-closed rule: if the ledger is unavailable the operation is REFUSED, and the
refusal is itself recorded on the channel surface.

Domain coverage (every one of these lands in the same ordered log):

===========================  =========================================
event                        mechanism
===========================  =========================================
message                      :func:`note_message`
mention resolution           :func:`note_mention`
dispatch / dispatch refusal  :func:`note_dispatch`
stage transition             :func:`note_stage`
bot create / clone           :func:`note_profile_created` (governance code)
re-scope / archive           governance codes via the lifecycle governor
acquired capability          :func:`note_capability_acquired`
self-modification            :func:`note_self_modification`
kill-switch activation       :func:`note_kill_switch`
===========================  =========================================

Authority actions reuse the existing ``GOVERNANCE_ACTIONS`` codes verbatim. No
new code is invented, because a code that the governance module does not know
is a code the governance module cannot refuse, validate or query.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.bots.events import OrgEventStore, get_org_event_store
from alpha.bots.governance_ledger import (
    ACTION_AUTHORITY_REFUSED,
    ACTION_DELEGATED,
    ACTION_PROFILE_INSTALLED,
    ACTION_SELF_MODIFICATION_PROPOSED,
    ACTION_SELF_MODIFICATION_RESULT,
    GOVERNANCE_ACTIONS,
    GovernanceLedgerError,
    assert_ledger_ordered_and_gap_free,
    record_governance_action,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# channel-surface event types. These share the ledger's seq counter; they are
# NOT governance actions, so they carry their own prefix and never collide with
# the validated ``governance.*`` namespace.
# ---------------------------------------------------------------------------
EV_MESSAGE = "channel.message"
EV_MENTION = "channel.mention"
EV_DISPATCH = "channel.dispatch"
EV_DISPATCH_REFUSED = "channel.dispatch_refused"
EV_STAGE = "channel.stage"
EV_CAPABILITY = "channel.capability_acquired"
EV_CAPABILITY_REFUSED = "channel.capability_refused"
EV_KILL_SWITCH = "channel.kill_switch"
EV_LEDGER_REFUSAL = "channel.ledger_refusal"
EV_TOOL_MODIFIED = "channel.tool_modified"

CHANNEL_EVENTS: frozenset[str] = frozenset(
    {
        EV_MESSAGE,
        EV_MENTION,
        EV_DISPATCH,
        EV_DISPATCH_REFUSED,
        EV_STAGE,
        EV_CAPABILITY,
        EV_CAPABILITY_REFUSED,
        EV_KILL_SWITCH,
        EV_LEDGER_REFUSAL,
        EV_TOOL_MODIFIED,
    }
)


class LedgerUnavailable(RuntimeError):
    """The ledger could not durably record an entry. The operation is refused."""


class LedgerRefused(RuntimeError):
    """The ledger rejected the entry. The operation is refused."""


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """A confirmed, durable ledger entry."""

    seq: int
    event_type: str
    actor: str
    target: str
    reason: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event_type": self.event_type,
            "actor": self.actor,
            "target": self.target,
            "reason": self.reason,
            "details": dict(self.details),
        }

    def human_line(self) -> str:
        """One line an operator can read without a JSON viewer."""
        detail = ""
        if self.details:
            detail = " " + " ".join(
                f"{k}={self.details[k]!r}" for k in sorted(self.details) if k != "reason"
            )
        return f"#{self.seq} {self.event_type} actor=@{self.actor} target={self.target} reason={self.reason!r}{detail}"


def _entry_from_event(event: Mapping[str, Any], *, reason: str) -> LedgerEntry:
    details = dict(event.get("details") or {})
    return LedgerEntry(
        seq=int(event.get("seq") or 0),
        event_type=str(event.get("event_type") or ""),
        actor=str(event.get("actor") or ""),
        target=str(event.get("target") or ""),
        reason=str(details.get("reason") or reason),
        details=details,
    )


def _on_disk(store: OrgEventStore, event: Mapping[str, Any]) -> bool:
    """Prove the entry is physically present in the ledger file.

    Scans for the exact ``(seq, event_type, id)`` triple. Reading the file
    rather than trusting the returned dict is the whole point: the store hands
    back a fully-formed event whether or not the write landed.
    """
    path = Path(store.log_path)
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or '"seq"' not in line:
                    continue
                try:
                    found = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    found.get("seq") == event.get("seq")
                    and found.get("id") == event.get("id")
                    and found.get("event_type") == event.get("event_type")
                ):
                    return True
    except OSError:
        return False
    return False


def append(
    event_type: str,
    *,
    actor: str,
    target: str,
    reason: str,
    details: dict[str, Any] | None = None,
    store: OrgEventStore | None = None,
    durable: bool = True,
) -> LedgerEntry:
    """Append one channel-surface event and confirm it is durable.

    ``durable=False`` is for tests that deliberately break the sink; production
    callers leave it on. A non-durable confirmation is still a confirmed entry
    in memory, which is exactly what the fail-closed test needs to distinguish.
    """
    if event_type not in CHANNEL_EVENTS:
        raise LedgerRefused(
            f"unknown channel event {event_type!r}; expected one of {sorted(CHANNEL_EVENTS)}"
        )
    missing = [
        name
        for name, value in (("actor", actor), ("target", target), ("reason", reason))
        if not str(value or "").strip()
    ]
    if missing:
        raise LedgerRefused(f"event {event_type!r} is missing required attribution: {missing}")

    sink = store or get_org_event_store()
    try:
        event = sink.append_event(
            event_type=event_type,
            actor=str(actor),
            target=str(target),
            details={"reason": str(reason).strip(), **(details or {})},
        )
    except Exception as exc:  # noqa: BLE001 - any sink fault must fail closed
        raise LedgerUnavailable(
            f"ledger append for {event_type!r} raised {type(exc).__name__}: {exc}; "
            "the operation is REFUSED rather than performed unrecorded"
        ) from exc

    if durable and not _on_disk(sink, event):
        raise LedgerUnavailable(
            f"ledger append for {event_type!r} (seq {event.get('seq')}) did not reach "
            f"{sink.log_path}; the operation is REFUSED rather than performed unrecorded"
        )
    return _entry_from_event(event, reason=reason)


def governance(
    action: str,
    *,
    actor: str,
    target: str,
    reason: str,
    details: dict[str, Any] | None = None,
    store: OrgEventStore | None = None,
    durable: bool = True,
) -> LedgerEntry:
    """Append a governance action through the existing validated code set."""
    if action not in GOVERNANCE_ACTIONS:
        raise LedgerRefused(
            f"unknown governance action {action!r}; expected one of {sorted(GOVERNANCE_ACTIONS)}"
        )
    sink = store or get_org_event_store()
    try:
        event = record_governance_action(
            action, actor=actor, target=target, reason=reason, details=details, store=sink
        )
    except GovernanceLedgerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LedgerUnavailable(
            f"governance append {action!r} raised {type(exc).__name__}: {exc}; the operation is REFUSED"
        ) from exc
    if durable and not _on_disk(sink, event):
        raise LedgerUnavailable(
            f"governance append {action!r} (seq {event.get('seq')}) did not reach "
            f"{sink.log_path}; the operation is REFUSED"
        )
    return _entry_from_event(event, reason=reason)


# ---------------------------------------------------------------------------
# domain helpers. Each returns the confirmed LedgerEntry.
#
# Every helper takes an explicit ``store`` keyword and FORWARDS it. That is not
# boilerplate: these were originally written with only ``**extra``, so a
# caller's ``store=`` was absorbed into the details dict, silently ignored as a
# store, and then JSON-encoded into the record. The failure mode was invisible
# (no error) and wrong (the entry landed in the process-global store instead of
# the one the caller was reading back). ``store`` is now a named parameter on
# every helper.
# ---------------------------------------------------------------------------
def note_message(
    actor: str,
    *,
    target: str,
    body_chars: int,
    seq: int,
    store: Any | None = None,
    **extra: Any,
) -> LedgerEntry:
    return append(
        EV_MESSAGE,
        actor=actor,
        target=target,
        reason=f"message seq={seq} accepted into the room",
        details={"seq": seq, "body_chars": int(body_chars), **extra},
        store=store,
    )


def note_mention(
    actor: str,
    *,
    target: str,
    resolution_line: str,
    ok: bool,
    store: Any | None = None,
    **extra: Any,
) -> LedgerEntry:
    return append(
        EV_MENTION,
        actor=actor,
        target=target,
        reason="mention resolved" if ok else "mention did not fully resolve; dispatched to resolved subset only",
        details={"resolution": resolution_line, "ok": bool(ok), **extra},
        store=store,
    )


def note_dispatch(
    actor: str,
    *,
    target: str,
    objective: str,
    reason: str,
    store: Any | None = None,
    **extra: Any,
) -> LedgerEntry:
    return governance(
        ACTION_DELEGATED,
        actor=actor,
        target=target,
        reason=reason,
        details={"objective": objective[:2000], **extra},
        store=store,
    )


def note_dispatch_refused(
    actor: str,
    *,
    target: str,
    reason: str,
    store: Any | None = None,
    **extra: Any,
) -> LedgerEntry:
    return append(
        EV_DISPATCH_REFUSED,
        actor=actor,
        target=target,
        reason=reason,
        details=dict(extra),
        store=store,
    )


def note_stage(
    run_id: str,
    *,
    actor: str,
    stage: str,
    previous: str,
    status: str,
    store: Any | None = None,
    **extra: Any,
) -> LedgerEntry:
    return append(
        EV_STAGE,
        actor=actor,
        target=run_id,
        reason=f"stage {previous} -> {stage} ({status})",
        details={"stage": stage, "previous_stage": previous, "status": status, **extra},
        store=store,
    )


def note_capability_acquired(
    actor: str,
    *,
    target: str,
    kind: str,
    sha256: str,
    source: str,
    store: Any | None = None,
    **extra: Any,
) -> LedgerEntry:
    return append(
        EV_CAPABILITY,
        actor=actor,
        target=target,
        reason=f"{kind} acquired after scan and independent approval",
        details={"kind": kind, "sha256": sha256, "source": source, **extra},
        store=store,
    )


def note_capability_refused(
    actor: str, *, target: str, reason: str, store: Any | None = None, **extra: Any
) -> LedgerEntry:
    return append(
        EV_CAPABILITY_REFUSED, actor=actor, target=target, reason=reason, details=dict(extra), store=store
    )


def note_tool_modified(
    actor: str,
    *,
    target: str,
    tool: str,
    before_sha: str,
    after_sha: str,
    diff: str,
    store: Any | None = None,
    **extra: Any,
) -> LedgerEntry:
    return append(
        EV_TOOL_MODIFIED,
        actor=actor,
        target=target,
        reason=f"tool {tool} modified under an explicit modification gate",
        details={
            "tool": tool,
            "before_sha256": before_sha,
            "after_sha256": after_sha,
            "diff": diff,
            **extra,
        },
        store=store,
    )


def note_self_modification(
    actor: str,
    *,
    target: str,
    result: str,
    reason: str,
    store: Any | None = None,
    **extra: Any,
) -> LedgerEntry:
    action = (
        ACTION_SELF_MODIFICATION_RESULT
        if result in {"applied", "rejected", "failed"}
        else ACTION_SELF_MODIFICATION_PROPOSED
    )
    return governance(
        action,
        actor=actor,
        target=target,
        reason=reason,
        details={"result": result, **extra},
        store=store,
    )


def note_kill_switch(
    actor: str,
    *,
    target: str,
    active: bool,
    reason: str,
    store: Any | None = None,
    **extra: Any,
) -> LedgerEntry:
    return append(
        EV_KILL_SWITCH,
        actor=actor,
        target=target,
        reason=reason,
        details={"active": bool(active), **extra},
        store=store,
    )


def note_ledger_refusal(
    actor: str, *, target: str, reason: str, store: Any | None = None, **extra: Any
) -> LedgerEntry:
    """Record that an operation was REFUSED because the ledger could not take it.

    This entry is best-effort by necessity: the sink is the thing that just
    failed. It is still attempted, and the refusal is additionally surfaced on
    the caller's own return value so it cannot vanish entirely.
    """
    try:
        return append(
            EV_LEDGER_REFUSAL,
            actor=actor,
            target=target,
            reason=reason,
            details=dict(extra),
            store=store,
            durable=False,
        )
    except Exception:  # noqa: BLE001 - the refusal is already being reported
        logger.error("Ledger unavailable; could not record the ledger refusal itself")
        raise


def note_profile_installed(
    actor: str, *, target: str, reason: str, store: Any | None = None, **extra: Any
) -> LedgerEntry:
    return governance(
        ACTION_PROFILE_INSTALLED, actor=actor, target=target, reason=reason, details=dict(extra), store=store
    )


def note_authority_refused(
    actor: str,
    *,
    target: str,
    reason: str,
    violations: list[str] | None = None,
    store: Any | None = None,
) -> LedgerEntry:
    return governance(
        ACTION_AUTHORITY_REFUSED,
        actor=actor,
        target=target,
        reason=reason,
        details={"violations": list(violations or [])},
        store=store,
    )


# ---------------------------------------------------------------------------
# reading + verification
# ---------------------------------------------------------------------------
def read_ledger(
    *,
    limit: int = 200,
    event_type: str | None = None,
    store: OrgEventStore | None = None,
) -> list[LedgerEntry]:
    """Read the ledger back, from disk, in sequence order.

    Reads the JSONL rather than the store's in-memory deque, so what an operator
    sees is what survived a restart.
    """
    sink = store or get_org_event_store()
    path = Path(sink.log_path)
    if not path.exists():
        return []
    out: list[LedgerEntry] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_type and raw.get("event_type") != event_type:
                continue
            out.append(_entry_from_event(raw, reason=""))
    out.sort(key=lambda e: e.seq)
    return out[-limit:]


def verify_ledger_ordered_and_gap_free(*, store: OrgEventStore | None = None) -> None:
    """Assert the durable ledger is ordered and gap-free.

    Delegates to the existing verifier in ``bots/governance_ledger`` so there is
    one definition of "gap-free" in this codebase, and reads from disk so the
    assertion covers what actually persisted.
    """
    sink = store or get_org_event_store()
    path = Path(sink.log_path)
    if not path.exists():
        return
    raw: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                raw.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    assert_ledger_ordered_and_gap_free(raw)
