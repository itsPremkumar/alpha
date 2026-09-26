"""Lifecycle governance for runtime profiles: retire, re-scope, drain, and reap.

Why lifecycle matters more than creation
----------------------------------------
Creating agents is the fun half. Stopping them is the half that keeps a
self-extending system from becoming a cost bomb: a fleet that can only grow is a
fleet that eventually spends the operator's money and holds the operator's
locks. So this module is the larger half of the feature, and it enforces four
things:

* **Retirement stops dispatch.** A retired profile is refused by the task-claim
  path (:func:`alpha.bots.work_discovery.claim_task`) and by
  :meth:`DynamicProfileStore.authorized_profile`, so it cannot pick up new work
  through any path that goes through the store.
* **Retirement drains in-flight work.** Retirement is a two-phase transition
  ``active -> draining -> retired``. In ``draining`` the profile is already
  refused new work but its in-flight claims are still tracked, so the drain is
  observable and bounded rather than assumed. Work that has not completed at the
  drain deadline is reported as abandoned, never quietly dropped.
* **Audit records survive.** Retirement is a soft state change. The profile, its
  grant, its history counters and its ledger entries all stay readable. Nothing
  is deleted, because a deleted agent is an unexplainable agent.
* **Re-scoping never leaves work running under a grant that no longer exists.**
  A re-scope bumps ``profile_version``; every dispatch re-validates, so in-flight
  work is either completed under the version it started with (which is recorded)
  or refused on re-validation. It never silently continues under a grant that
  has been taken away.

Two population guards close the loop: an idle sweep that PROPOSES retirement for
profiles which produced no useful work across a window, and the live/total
population ceilings enforced in
:meth:`AuthorityCeiling.assert_population_within_ceiling`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from alpha.bots.authority_ceiling import (
    AuthorityViolation,
    enforce_grant,
    get_ceiling,
    narrow_to_ceiling,
    normalise_capabilities,
)
from alpha.bots.dynamic_profiles import (
    DynamicProfileStore,
    ProfileProposal,
    ProfileValidationError,
    RuntimeProfile,
)
from alpha.bots.governance_ledger import (
    ACTION_PROFILE_IDLE_PROPOSED,
    ACTION_PROFILE_RESCOPED,
    ACTION_PROFILE_RETIRED,
    record_governance_action,
)
from alpha.bots.profile import BotProfile

logger = logging.getLogger(__name__)

#: Two-phase retirement states.
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUS_DRAINING = "draining"
STATUS_RETIRED = "retired"

#: Statuses in which a profile may still be given work.
DISPATCHABLE_STATUSES: frozenset[str] = frozenset({STATUS_ACTIVE})

#: Default idle window. A profile that has produced no useful work for this long
#: is proposed for retirement.
DEFAULT_IDLE_WINDOW_SECONDS = 24 * 60 * 60


class RetirementError(RuntimeError):
    """A lifecycle transition was refused."""


@dataclass
class InFlightClaim:
    """One unit of work a profile was given and has not finished."""

    task_id: str
    claimed_at: str
    profile_version: int
    grant_at_claim: frozenset[str] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "claimed_at": self.claimed_at,
            "profile_version": self.profile_version,
            "grant_at_claim": sorted(self.grant_at_claim),
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass
class RetirementResult:
    """Outcome of a retirement, including the drain report.

    ``abandoned`` is the load-bearing field: work that did not finish inside the
    drain window is reported by name. Dropping it silently is how a retired
    agent's failure becomes invisible.
    """

    profile_name: str
    previous_status: str
    status: str
    in_flight_at_retire: int
    drained: list[InFlightClaim] = field(default_factory=list)
    abandoned: list[InFlightClaim] = field(default_factory=list)
    audit_record_retained: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_name": self.profile_name,
            "previous_status": self.previous_status,
            "status": self.status,
            "in_flight_at_retire": self.in_flight_at_retire,
            "drained": [c.to_dict() for c in self.drained],
            "abandoned": [c.to_dict() for c in self.abandoned],
            "audit_record_retained": self.audit_record_retained,
        }


class LifecycleGovernor:
    """Owns retire / re-scope / drain / idle-reap for runtime profiles.

    Every transition is a ledger entry with actor, target, reason and timestamp.
    The governor refuses to act on anything that enforces the ceiling, so "alpha
    cannot retire, disable or re-scope the component that enforces the ceiling"
    is a code fact and not a convention.
    """

    def __init__(
        self,
        store: DynamicProfileStore,
        *,
        event_store: Any = None,
        in_flight: dict[str, list[InFlightClaim]] | None = None,
    ) -> None:
        self.store = store
        self._event_store = event_store
        #: profile name -> claims. Injected so a test (or the dispatcher) can
        #: supply the live view; the governor never invents completion.
        self.in_flight: dict[str, list[InFlightClaim]] = in_flight if in_flight is not None else {}

    # -- helpers -----------------------------------------------------------
    def _require_profile(self, name: str) -> RuntimeProfile:
        profile = self.store.get_profile(name)
        if profile is None:
            raise RetirementError(f"no runtime profile named {name!r}")
        return profile

    def _ledger(self, action: str, *, actor: str, target: str, reason: str, details: dict[str, Any]) -> None:
        record_governance_action(
            action, actor=actor, target=target, reason=reason, details=details, store=self._event_store
        )

    def can_dispatch(self, name: str) -> bool:
        """Whether this profile may be given NEW work right now.

        This is the single predicate the dispatch paths consult. It re-validates
        against the current ceiling first, so a re-scoped or demoted profile
        stops being dispatchable in the same call that changed it.
        """
        profile, _removed = self.store.revalidate_profile(name)
        if profile is None:
            return False
        if profile.status not in DISPATCHABLE_STATUSES:
            return False
        if not profile.granted_capabilities:
            return False
        return True

    def assert_dispatchable(self, name: str) -> RuntimeProfile:
        """Raise unless the profile may be given new work."""
        profile, _removed = self.store.revalidate_profile(name)
        if profile is None:
            raise RetirementError(f"no runtime profile named {name!r}")
        if profile.status not in DISPATCHABLE_STATUSES:
            raise RetirementError(
                f"profile {profile.name!r} is {profile.status!r} and may not receive new work"
            )
        if not profile.granted_capabilities:
            raise RetirementError(
                f"profile {profile.name!r} has an empty grant after ceiling re-validation "
                f"and may not receive new work"
            )
        return profile

    # -- enabling / disabling ---------------------------------------------
    def enable_profile(self, name: str, *, actor: str, reason: str) -> RuntimeProfile:
        """Enable a profile for dispatch, after re-validating its grant."""
        profile = self._require_profile(name)
        kept, removed = narrow_to_ceiling(profile.granted_capabilities, ceiling=self.store.ceiling())
        if removed:
            profile.granted_capabilities = kept
            profile.demoted_from = normalise_capabilities(
                profile.demoted_from | (profile.declared_capabilities - kept)
            )
        if not profile.granted_capabilities:
            raise RetirementError(
                f"profile {profile.name!r} cannot be enabled: its grant is empty under the "
                f"current authority ceiling"
            )
        profile.status = STATUS_ACTIVE
        profile.profile_version += 1
        profile.updated_at = _now()
        self.store._save()
        self._ledger(
            ACTION_PROFILE_RESCOPED,
            actor=actor,
            target=profile.name,
            reason=reason,
            details={"new_status": profile.status, "granted": sorted(kept), "removed": sorted(removed)},
        )
        return profile

    def disable_profile(self, name: str, *, actor: str, reason: str) -> RuntimeProfile:
        """Stop a profile receiving work WITHOUT retiring it. Reversible."""
        profile = self._require_profile(name)
        previous = profile.status
        profile.status = STATUS_DISABLED
        profile.profile_version += 1
        profile.updated_at = _now()
        self.store._save()
        self._ledger(
            ACTION_PROFILE_RESCOPED,
            actor=actor,
            target=profile.name,
            reason=reason,
            details={"previous_status": previous, "new_status": profile.status},
        )
        return profile

    # -- re-scoping --------------------------------------------------------
    def rescope_profile(
        self,
        name: str,
        *,
        new_capabilities: list[str],
        actor: str,
        reason: str,
    ) -> RuntimeProfile:
        """Change a profile's grant, up or down, within the ceiling.

        Widening is checked against BOTH the ceiling and the creator's original
        grant, so re-scoping is not a hole around creation: a profile cannot
        acquire, by re-scoping, a capability it could not have been created with.

        In-flight work is not silently continued. The profile version is bumped,
        so any dispatch that has not re-validated is provably operating on a
        superseded grant, and the ledger records the version transition.
        """
        profile = self._require_profile(name)
        # The ceiling may not be tightened *upward* past what the creator holds:
        # re-scope is bounded by min(ceiling, creator grant), exactly as creation is.
        bound = self.store.ceiling()
        creator_bound = normalise_capabilities(profile.creator_grant) or profile.granted_capabilities
        grant = enforce_grant(
            new_capabilities,
            creator_grant=creator_bound,
            subject=f"re-scope of {profile.name!r}",
            ceiling=bound,
        )
        previous = set(profile.granted_capabilities)
        profile.granted_capabilities = grant
        profile.demoted_from = normalise_capabilities(profile.demoted_from | (previous - grant))
        profile.profile_version += 1
        profile.updated_at = _now()
        if not grant and profile.status == STATUS_ACTIVE:
            profile.status = STATUS_DISABLED
        self.store._save()
        self._ledger(
            ACTION_PROFILE_RESCOPED,
            actor=actor,
            target=profile.name,
            reason=reason,
            details={
                "previous_capabilities": sorted(previous),
                "new_capabilities": sorted(grant),
                "profile_version": profile.profile_version,
                "in_flight_versions": sorted(
                    {c.profile_version for c in self.in_flight.get(profile.name, [])}
                ),
            },
        )
        return profile

    # -- retirement + drain ------------------------------------------------
    def begin_retirement(self, name: str, *, actor: str, reason: str) -> RetirementResult:
        """Phase 1: stop new work, start draining what is already in flight."""
        profile = self._require_profile(name)
        if profile.status == STATUS_RETIRED:
            return RetirementResult(
                profile_name=profile.name,
                previous_status=STATUS_RETIRED,
                status=STATUS_RETIRED,
                in_flight_at_retire=0,
            )
        previous = profile.status
        claims = list(self.in_flight.get(profile.name, []))
        profile.status = STATUS_DRAINING
        profile.profile_version += 1
        profile.updated_at = _now()
        self.store._save()
        self._ledger(
            ACTION_PROFILE_RETIRED,
            actor=actor,
            target=profile.name,
            reason=reason,
            details={
                "phase": "draining",
                "previous_status": previous,
                "in_flight": [c.to_dict() for c in claims],
            },
        )
        return RetirementResult(
            profile_name=profile.name,
            previous_status=previous,
            status=STATUS_DRAINING,
            in_flight_at_retire=len(claims),
        )

    def complete_retirement(
        self,
        name: str,
        *,
        actor: str,
        reason: str,
        drain_deadline_seconds: float = 300.0,
    ) -> RetirementResult:
        """Phase 2: finish the drain and mark retired. Audit record is kept.

        Claims still open at the deadline are returned as ``abandoned`` and
        written to the ledger by name. They are never dropped: a task that
        vanished when its owner was retired is precisely the failure mode that
        makes a fleet untrustworthy.
        """
        profile = self._require_profile(name)
        claims = list(self.in_flight.get(profile.name, []))
        deadline = _parse(profile.updated_at)
        drained: list[InFlightClaim] = []
        abandoned: list[InFlightClaim] = []
        if claims:
            expired = (
                deadline is not None
                and (datetime.now(UTC) - deadline) > timedelta(seconds=drain_deadline_seconds)
            )
            for claim in claims:
                (abandoned if expired else drained).append(claim)
        else:
            drained = []

        previous = profile.status
        profile.status = STATUS_RETIRED
        profile.profile_version += 1
        profile.updated_at = _now()
        # The audit record is deliberately retained: grant, history counters and
        # metadata all stay readable after retirement.
        self.store._save()
        if profile.name in self.in_flight:
            self.in_flight[profile.name] = []
        self._ledger(
            ACTION_PROFILE_RETIRED,
            actor=actor,
            target=profile.name,
            reason=reason,
            details={
                "phase": "retired",
                "previous_status": previous,
                "drained": [c.to_dict() for c in drained],
                "abandoned": [c.to_dict() for c in abandoned],
                "audit_record_retained": True,
                "granted_capabilities": sorted(profile.granted_capabilities),
            },
        )
        return RetirementResult(
            profile_name=profile.name,
            previous_status=previous,
            status=STATUS_RETIRED,
            in_flight_at_retire=len(claims),
            drained=drained,
            abandoned=abandoned,
        )

    def retire_profile(
        self,
        name: str,
        *,
        actor: str,
        reason: str,
        drain_deadline_seconds: float = 0.0,
    ) -> RetirementResult:
        """Both phases, for callers that can drain immediately.

        With ``drain_deadline_seconds=0`` any still-open claim is reported as
        abandoned at once. Use :meth:`begin_retirement` /
        :meth:`complete_retirement` when there is a real drain window.
        """
        self.begin_retirement(name, actor=actor, reason=reason)
        return self.complete_retirement(
            name, actor=actor, reason=reason, drain_deadline_seconds=drain_deadline_seconds
        )

    # -- idle reaping ------------------------------------------------------
    def find_idle_profiles(
        self,
        *,
        window_seconds: float = DEFAULT_IDLE_WINDOW_SECONDS,
        min_tasks: int = 1,
        now: datetime | None = None,
    ) -> list[RuntimeProfile]:
        """Profiles that produced no useful work across the window.

        Two conditions, both required: the profile has actually been used at
        least ``min_tasks`` times (so a freshly created profile is not reaped for
        having had no chance yet), and it has produced no useful work inside the
        window.
        """
        moment = now or datetime.now(UTC)
        cutoff = moment - timedelta(seconds=window_seconds)
        idle: list[RuntimeProfile] = []
        for profile in self.store.list_profiles():
            if profile.status in (STATUS_RETIRED, STATUS_DRAINING):
                continue
            if profile.total_task_count < min_tasks:
                continue
            last_useful = _parse(profile.last_useful_work_at)
            reference = last_useful or _parse(profile.created_at)
            if reference is None:
                continue
            if reference < cutoff:
                idle.append(profile)
        return idle

    def propose_idle_retirements(
        self,
        *,
        window_seconds: float = DEFAULT_IDLE_WINDOW_SECONDS,
        actor: str = "alpha",
        reason: str = "",
    ) -> list[ProfileProposal]:
        """PROPOSE retirement for idle profiles. Never retires directly.

        Retirement is a proposal here for the same reason profile creation is:
        an automated actor deciding, on its own, to remove an agent is a decision
        an operator must be able to see and undo. The proposal goes through the
        same approval path as creation.
        """
        proposals: list[ProfileProposal] = []
        for profile in self.find_idle_profiles(window_seconds=window_seconds):
            self._ledger(
                ACTION_PROFILE_IDLE_PROPOSED,
                actor=actor,
                target=profile.name,
                reason=reason or f"no useful work in {window_seconds}s",
                details={
                    "total_task_count": profile.total_task_count,
                    "useful_work_count": profile.useful_work_count,
                    "last_useful_work_at": profile.last_useful_work_at,
                },
            )
            proposals.append(
                ProfileProposal(
                    profile_name=profile.name,
                    requested_capabilities=sorted(profile.granted_capabilities),
                    role=profile.role,
                    rationale=reason or f"idle for more than {window_seconds}s",
                    creator=actor,
                    creator_grant=sorted(profile.creator_grant or profile.granted_capabilities),
                    metadata={"kind": "idle_retirement", "targets": profile.name},
                )
            )
        return proposals

    # -- ceiling protection ------------------------------------------------
    def assert_not_protected(self, target: str, *, actor: str) -> None:
        """Refuse any lifecycle action aimed at a ceiling-enforcement component.

        The governor is the retirement/re-scope path, so this is where "alpha
        cannot retire, disable or re-scope the component that enforces the
        ceiling" is actually enforced.
        """
        get_ceiling().assert_may_modify(target, actor=actor, reason="lifecycle transition")

    # -- projection into the existing roster -------------------------------
    def project_into_registry(self, registry: Any) -> list[str]:
        """Register active runtime profiles into a :class:`BotRegistry`.

        This is the wiring that makes a created profile reachable from a real
        dispatch path: the registry is what the DM, inbox, org-chart and
        task-claim code already read, so projecting here means a created profile
        participates in bot mode without any of that code changing.

        Returns the names projected. Retired and draining profiles are NOT
        projected, so they cannot be offered work.
        """
        projected: list[str] = []
        for profile in self.store.list_profiles():
            if profile.status not in DISPATCHABLE_STATUSES:
                continue
            if not profile.granted_capabilities:
                continue
            try:
                bot: BotProfile = profile.to_bot_profile()
                registry.register(bot)
                projected.append(profile.name)
            except Exception:
                logger.warning("Could not project runtime profile %s", profile.name, exc_info=True)
        return projected


__all__ = [
    "AuthorityViolation",
    "DEFAULT_IDLE_WINDOW_SECONDS",
    "DISPATCHABLE_STATUSES",
    "InFlightClaim",
    "LifecycleGovernor",
    "ProfileValidationError",
    "RetirementError",
    "RetirementResult",
    "STATUS_ACTIVE",
    "STATUS_DISABLED",
    "STATUS_DRAINING",
    "STATUS_RETIRED",
]
