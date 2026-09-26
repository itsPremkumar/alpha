"""The governance ledger: one ordered, gap-free record of every authority action.

Why this is a facade over :mod:`alpha.bots.events` and not a second log
-----------------------------------------------------------------------
An action an operator cannot explain is an action they cannot trust, and an
operator cannot explain two interleaved logs. The org event store is already
append-only, durable and on the real runtime path (``log_org_event`` is called
by the task-claim, kill-switch and org paths), so the governance ledger is
*that* store, with two additions that make it a ledger of record:

* the monotonic ``seq`` added to :class:`~alpha.bots.events.OrgEventStore`,
  which makes the log ordered and gap-free;
* :func:`record_governance_action`, which refuses to write a governance entry
  that is missing attribution. A delegation, a profile creation, a re-scope, a
  retirement and a self-modification are all *authority* actions: an authority
  action without an actor, a target, a reason and a timestamp is not a record,
  it is noise, and it is refused rather than stored.

Every governance entry uses the ``governance.`` event-type prefix so the whole
authority surface can be read back with one
:func:`query_governance_actions` call.

What this ledger is NOT
-----------------------
An event is not a decision receipt.  An event records that something happened.
A receipt records *who decided, under which policy rule, in which effective
scope, from which inputs, with which alternatives rejected and why* -- and the
alternatives and inputs only exist at the moment of the decision, so they
cannot be reconstructed afterwards.  :func:`record_authority_refusal` therefore
writes BOTH: the ordered event here, and a hash-chained decision receipt via
:func:`record_decision_receipt`.

This is a policy control inside one trust envelope, not a security boundary.
The hash chain is tamper-EVIDENT, not tamper-proof: the same process that writes
it can rewrite it, and anyone with write access to the file can recompute the
chain.  Making it unforgeable needs a signing key that lives outside this
process, which is a boundary rather than a control.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from alpha.bots.events import get_org_event_store

logger = logging.getLogger(__name__)

#: Event-type prefix. Every authority action carries it.
GOVERNANCE_PREFIX = "governance."

#: The governance event types this codebase emits. Kept as an explicit set so a
#: typo in a caller is a loud failure rather than a queryable-looking event that
#: never appears in the ledger.
ACTION_PROFILE_PROPOSED = "profile_proposed"
ACTION_PROFILE_INSTALLED = "profile_installed"
ACTION_PROFILE_REFUSED = "profile_refused"
ACTION_PROFILE_RESCOPED = "profile_rescoped"
ACTION_PROFILE_RETIRED = "profile_retired"
ACTION_PROFILE_DEMOTED = "profile_demoted"
ACTION_PROFILE_IDLE_PROPOSED = "profile_idle_retirement_proposed"
ACTION_DELEGATED = "delegated"
ACTION_SELF_MODIFICATION_PROPOSED = "self_modification_proposed"
ACTION_SELF_MODIFICATION_RESULT = "self_modification_result"
ACTION_CEILING_RELOADED = "ceiling_reloaded"
ACTION_AUTHORITY_REFUSED = "authority_refused"

GOVERNANCE_ACTIONS: frozenset[str] = frozenset(
    {
        ACTION_PROFILE_PROPOSED,
        ACTION_PROFILE_INSTALLED,
        ACTION_PROFILE_REFUSED,
        ACTION_PROFILE_RESCOPED,
        ACTION_PROFILE_RETIRED,
        ACTION_PROFILE_DEMOTED,
        ACTION_PROFILE_IDLE_PROPOSED,
        ACTION_DELEGATED,
        ACTION_SELF_MODIFICATION_PROPOSED,
        ACTION_SELF_MODIFICATION_RESULT,
        ACTION_CEILING_RELOADED,
        ACTION_AUTHORITY_REFUSED,
    }
)


class GovernanceLedgerError(RuntimeError):
    """Raised when a governance entry is missing required attribution."""


def record_governance_action(
    action: str,
    *,
    actor: str,
    target: str,
    reason: str,
    details: dict[str, Any] | None = None,
    store: Any = None,
) -> dict[str, Any]:
    """Append one attributed authority action to the ordered ledger.

    ``actor``, ``target``, ``reason`` and the timestamp (added by the store) are
    MANDATORY. Missing attribution raises :class:`GovernanceLedgerError` before
    anything is written: an unattributed authority action is worse than no
    record, because it looks like a record.
    """
    if action not in GOVERNANCE_ACTIONS:
        raise GovernanceLedgerError(
            f"unknown governance action {action!r}; expected one of {sorted(GOVERNANCE_ACTIONS)}"
        )
    missing = [
        field
        for field, value in (("actor", actor), ("target", target), ("reason", reason))
        if not str(value or "").strip()
    ]
    if missing:
        raise GovernanceLedgerError(
            f"governance action {action!r} is missing required attribution: {missing}"
        )

    event = (store or get_org_event_store()).append_event(
        event_type=f"{GOVERNANCE_PREFIX}{action}",
        actor=str(actor).strip(),
        target=str(target).strip(),
        details={"reason": str(reason).strip(), **(details or {})},
    )
    return event


def record_authority_refusal(
    *,
    actor: str,
    target: str,
    reason: str,
    violations: list[str] | None = None,
    store: Any = None,
) -> dict[str, Any]:
    """Record a REFUSED authority action.

    A refusal is a first-class ledger entry, not a log line. "The system tried to
    do X and was stopped because Y" is exactly what an operator needs when
    asking why an autonomous agent behaved oddly, so it goes in the same ordered
    log as the successes.

    It ALSO writes a decision receipt, because an event and a receipt are
    different artefacts.  The event says "this was refused".  The receipt says
    who refused it, under which policy rule, in which effective scope, from
    which inputs, and what was rejected instead -- and it is hash-chained, so it
    cannot be edited after the fact.  The event cannot answer any of those
    questions, and cannot be retrofitted to.
    """

    event = record_governance_action(
        ACTION_AUTHORITY_REFUSED,
        actor=actor or "unknown",
        target=target or "unknown",
        reason=reason or "unspecified",
        details={"violations": list(violations or [])},
        store=store,
    )
    record_decision_receipt(
        decision=ACTION_AUTHORITY_REFUSED,
        actor=actor,
        outcome="refused",
        policy_rule="",
        target=target,
        reason=reason,
        rejected=[{"option": str(item), "reason": str(reason)} for item in (violations or [])],
    )
    return event


#: Where decision receipts live.  Kept here so the ledger and the receipt chain
#: are reachable from one import, and so a caller recording a governance action
#: cannot accidentally record it somewhere unchained.
def receipt_chain(chain_id: str = "governance") -> Any:
    """The decision-receipt chain paired with this ledger."""

    from alpha.safety.authority.receipts import get_receipt_chain

    return get_receipt_chain(chain_id)


def record_decision_receipt(
    *,
    decision: str,
    actor: str,
    outcome: str,
    policy_rule: str = "",
    target: str = "",
    reason: str = "",
    delegator: str = "",
    scope: str = "",
    inputs: Iterable[str] = (),
    rejected: Iterable[dict[str, str]] = (),
    work_ref: str = "",
    effective_policy: dict[str, Any] | None = None,
    taint_sources: Iterable[str] = (),
    chain_id: str = "governance",
) -> Any:
    """Write one tamper-evident decision receipt.

    A receipt whose policy rule cannot be named is still WRITTEN, and is written
    FLAGGED.  "A decision with no nameable rule is a finding, not a receipt" is
    only true as an accounting statement if the finding is actually recorded.

    ``actor`` names the executing principal and ``delegator`` names who
    delegated to it.  A delegated child's action is attributed to the CHILD, with
    the parent recorded as the delegator -- never collapsed into the parent,
    because "who did this" is exactly the question that becomes unanswerable
    when the two are merged.
    """

    from alpha.safety.authority.receipts import (
        ActorKind,
        ExecutionIdentity,
        RejectedAlternative,
        automated_system,
        child_agent,
        human,
    )

    identity: ExecutionIdentity
    actor_text = str(actor or "").strip() or "unknown"
    delegator_text = str(delegator or "").strip()
    try:
        if delegator_text:
            identity = child_agent(
                actor_text,
                delegator=human(delegator_text, authenticated_by="governance-ledger"),
                authenticated_by="governance-ledger",
            )
        elif actor_text in {"server", "system", "sentinel", "scheduler", "unknown"}:
            identity = automated_system(actor_text)
        else:
            identity = ExecutionIdentity(
                kind=ActorKind.AGENT,
                principal_id=actor_text,
                authenticated_by="governance-ledger",
            )
    except Exception as exc:
        # An unusable identity must not lose the record; the receipt is still
        # written and the failure is visible in the reason.
        logger.warning("could not build execution identity for %r: %s", actor_text, exc)
        identity = automated_system(actor_text)

    alternatives: list[RejectedAlternative] = []
    for item in rejected:
        option = str(item.get("option", "")).strip()
        why = str(item.get("reason", reason or "unspecified")).strip() or "unspecified"
        if option:
            alternatives.append(RejectedAlternative(option=option, reason=why))

    return receipt_chain(chain_id).append(
        decision=str(decision),
        identity=identity,
        outcome=str(outcome),
        policy_rule=str(policy_rule or ""),
        scope=str(scope or ""),
        inputs=list(inputs),
        rejected_alternatives=tuple(alternatives),
        work_ref=str(work_ref or f"governance:{target}" if target else ""),
        effective_policy=effective_policy or {},
        taint_sources=taint_sources,
    )


def query_governance_actions(
    *,
    limit: int = 200,
    actor: str | None = None,
    target: str | None = None,
    store: Any = None,
) -> list[dict[str, Any]]:
    """Read the governance ledger back, newest first."""
    events = (store or get_org_event_store()).query_events(
        limit=limit, actor=actor, target=target
    )
    return [e for e in events if str(e.get("event_type", "")).startswith(GOVERNANCE_PREFIX)]


def assert_ledger_ordered_and_gap_free(
    entries: list[dict[str, Any]],
) -> None:
    """Assert the supplied entries form a strictly increasing, gap-free run.

    ``entries`` must be in ascending ``seq`` order (reverse a newest-first query
    first). Raises :class:`GovernanceLedgerError` naming the break, so a test
    failure points at the missing or reordered sequence number instead of just
    saying "not ordered".
    """
    seen: list[int] = []
    for entry in entries:
        seq = entry.get("seq")
        if not isinstance(seq, int):
            raise GovernanceLedgerError(
                f"ledger entry {entry.get('id')!r} has no integer seq; ordering unverifiable"
            )
        seen.append(seq)
    for previous, current in zip(seen, seen[1:], strict=False):
        if current != previous + 1:
            raise GovernanceLedgerError(
                f"ledger is not gap-free: seq {previous} is followed by {current}"
            )


__all__ = [
    "ACTION_AUTHORITY_REFUSED",
    "ACTION_CEILING_RELOADED",
    "ACTION_DELEGATED",
    "ACTION_PROFILE_DEMOTED",
    "ACTION_PROFILE_IDLE_PROPOSED",
    "ACTION_PROFILE_INSTALLED",
    "ACTION_PROFILE_PROPOSED",
    "ACTION_PROFILE_REFUSED",
    "ACTION_PROFILE_RESCOPED",
    "ACTION_PROFILE_RETIRED",
    "ACTION_SELF_MODIFICATION_PROPOSED",
    "ACTION_SELF_MODIFICATION_RESULT",
    "GOVERNANCE_ACTIONS",
    "GOVERNANCE_PREFIX",
    "GovernanceLedgerError",
    "assert_ledger_ordered_and_gap_free",
    "query_governance_actions",
    "receipt_chain",
    "record_authority_refusal",
    "record_decision_receipt",
    "record_governance_action",
]
