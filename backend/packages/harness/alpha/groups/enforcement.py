"""Opt-in strict enforcement: advisory coordination that can actually refuse.

`CollaborationConfig.lock_policy` has accepted `"strict"` since it was written,
and **nothing has ever read it**. It is the only fully decorative setting in the
collaboration config, which makes it worse than absent: an operator who sets it
believes writes are protected, and discovers otherwise when two agents clobber
each other.

This module is what makes it mean something.

## Why the refusal lives here and not in `locks.py`

`projects/locks.py` already owns *exclusive ownership* of a project path, with a
reviewed refusal shape (HTTP 423 carrying the holder). It is the right owner of
that guarantee, and this module does not replace or weaken it.

What was missing is the **policy decision**: nobody asked the collaboration
config whether a claim should be honoured. So this module reads the policy,
consults the claim layer, and returns a decision. Refusal enforcement itself is
`LockManager.acquire()`, unchanged.

## The two things strict mode must not do

**Refuse on a soft signal.** It refuses only against a *live* claim or lock. An
orphaned claim — one whose holder is confirmed crashed, or whose process is
gone — is **available**, and refusing there would leave a dead agent's work
locked forever. That is the failure a naive "respect every claim" rule produces,
and it is worse than not enforcing at all.

**Refuse on a stale claim.** The lease is 120 seconds. A claim past it is not
held by anybody, and strict mode sweeps before it decides so a decision is never
made against a lapsed record.

## Default

`advisory`, which is exactly today's behaviour. Nothing changes unless an
operator opts in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

LockPolicy = Literal["advisory", "strict"]

#: Under strict mode a confirmed-dead holder's *lock* is shortened to this, so a
#: crash does not leave a path blocked for the remainder of the 1800s TTL. Not
#: zero: the crash is evidence, and a peer that wants the file should take it
#: deliberately rather than find it already handed to somebody else.
CRASHED_LOCK_TTL_SECONDS = 120.0


@dataclass
class EnforcementDecision:
    """What to do about one write, and why.

    `allowed` is the answer. `reason` is a stable machine word so a test asserts
    the policy rather than the prose, and it is always present — a refusal that
    cannot explain itself is indistinguishable from a bug.
    """

    allowed: bool
    reason: str
    detail: str = ""
    holder: str | None = None
    holder_state: str | None = None
    subject: str = ""
    claim_ids: list[str] = field(default_factory=list)
    #: A claim the writer could take over instead of being refused.
    reclaimable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "detail": self.detail,
            "holder": self.holder,
            "holder_state": self.holder_state,
            "subject": self.subject,
            "claim_ids": list(self.claim_ids),
            "reclaimable": self.reclaimable,
        }


@dataclass
class HolderLiveness:
    """Enough to tell a live holder from an abandoned one."""

    activity: str | None = None
    alive: bool | None = None

    @property
    def gone(self) -> bool:
        """Confirmed dead, or measured gone.

        `unresponsive` is deliberately NOT enough: an agent inside a long tool
        call is silent but very much alive, and taking its work away would hand
        live work to a second agent.
        """
        if self.activity == "crashed":
            return True
        if self.alive is False:
            return True
        return False


class StrictEnforcer:
    """Decides whether a write may proceed, under the room's configured policy."""

    def __init__(self, room_name: str, policy: str = "advisory"):
        self.room_name = room_name
        self.policy: LockPolicy = "strict" if str(policy or "advisory").strip().lower() == "strict" else "advisory"

    # -- reads ---------------------------------------------------------------

    def policy_for_project(self, project_id: str | None) -> str:
        """Resolve the effective policy.

        A project-linked room reads its real `CollaborationConfig`. A plain
        group room has no project, and falls back to `advisory` — a room nobody
        configured must not start refusing writes.
        """
        if self.policy == "strict":
            return "strict"
        if not project_id:
            return "advisory"
        try:
            from alpha.projects.crew import get_crew_service

            return get_crew_service().get_collaboration(project_id).lock_policy
        except Exception:
            logger.debug("Collaboration policy unreadable for %s; defaulting to advisory", project_id, exc_info=True)
            return "advisory"

    def check(
        self,
        subject: str,
        writer: str,
        *,
        holder_states: dict[str, str] | None = None,
        project_id: str | None = None,
    ) -> EnforcementDecision:
        """May `writer` proceed on `subject`?

        Always returns a decision with a reason. Never raises — a coordination
        store that is unavailable must not be able to refuse or allow a write on
        its behalf, and the safe answer in both directions is to defer to the
        advisory behaviour that already exists.
        """
        policy = self.policy_for_project(project_id)
        if policy != "strict":
            return EnforcementDecision(allowed=True, reason="advisory_policy", detail="claims are advisory in this room")

        try:
            from alpha.groups.claims import get_claim_store, normalise_subject, subjects_overlap

            clean = normalise_subject(subject, "file")
            if not clean:
                return EnforcementDecision(allowed=True, reason="no_subject", subject=str(subject or ""))

            store = get_claim_store()
            # Sweep first: a decision must never be made against a lapsed lease.
            store.sweep()

            owner = (writer or "").lower().strip()
            states = holder_states or {}

            # Iterate the room's live claims directly rather than going through
            # `detect_soft_conflicts`. That helper needs *two* different
            # holders by construction, but the case strict mode exists to catch
            # is exactly ONE other agent holding the file — which is not a
            # "conflict" to anybody, only a collision waiting to happen.
            live = store.room_claims(self.room_name, live_only=True)
            for claim in live:
                if claim.room_name != self.room_name:
                    continue
                if claim.holder == owner:
                    continue
                if not subjects_overlap(claim.kind, claim.subject, "file", clean):
                    continue

                if HolderLiveness(states.get(claim.holder)).gone:
                    # Available work is not blocked work. Refusing here would
                    # leave a dead agent's file locked indefinitely.
                    return EnforcementDecision(
                        allowed=True,
                        reason="holder_gone",
                        detail=f"{claim.holder} is gone; the claim is available to take over",
                        holder=claim.holder,
                        holder_state=states.get(claim.holder),
                        subject=clean,
                        claim_ids=[claim.claim_id],
                        reclaimable=True,
                    )
                return EnforcementDecision(
                    allowed=False,
                    reason="live_claim",
                    detail=f"{claim.holder} has claimed {claim.subject}; request access or wait for release",
                    holder=claim.holder,
                    holder_state=states.get(claim.holder),
                    subject=clean,
                    claim_ids=[claim.claim_id],
                )

            return EnforcementDecision(allowed=True, reason="no_conflict", subject=clean)
        except Exception:
            logger.warning("Strict enforcement failed for %s; deferring to advisory", subject, exc_info=True)
            return EnforcementDecision(allowed=True, reason="enforcement_unavailable", detail="coordination store unreadable; advisory behaviour retained")


def crashed_lock_ttl(policy: str, holder_state: str | None) -> float | None:
    """A shortened TTL for a lock whose owner is confirmed dead.

    Returns `None` when nothing should change, so the caller cannot accidentally
    shorten a live holder's lease.
    """
    if str(policy or "").strip().lower() != "strict":
        return None
    if holder_state != "crashed":
        return None
    return CRASHED_LOCK_TTL_SECONDS
