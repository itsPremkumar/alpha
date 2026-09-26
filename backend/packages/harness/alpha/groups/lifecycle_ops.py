"""The full bot lifecycle, end to end, as one fail-closed facade.

Reuses, and does not reimplement:

* :mod:`alpha.bots.dynamic_profiles` - propose / approve / install / revalidate
* :mod:`alpha.bots.cloning` - :class:`BotCloneEngine`, the registry-level copy
* :mod:`alpha.bots.lifecycle_governor` - re-scope, drain, retire, idle proposals
* :mod:`alpha.bots.authority_ceiling` - ``enforce_grant``, ``narrow_to_ceiling``,
  ``assert_may_modify``, population bounds
* :mod:`alpha.channels.ledger` - the one ordered ledger, with a durability
  read-back so an unrecorded action is refused

What this facade adds, and only this:

* **Lineage as a record, not a dict key.** ``bots/cloning.py`` writes
  ``cloned_from`` / ``clone_mode`` / ``lineage`` into an untyped metadata dict
  with no timestamp and no change diff. :class:`CloneLineage` here records
  parent, ISO timestamp, and a field-by-field before/after diff.
* **Archive that is reversible and transcript-preserving.** Retirement is the
  existing two-phase drain; the reversibility and the transcript retention are
  this module's job, because ``complete_retirement`` correctly preserves the
  profile but knows nothing about chat transcripts.
* **The ceiling is checked on the way IN as well as on the way OUT.** A
  tightening is only meaningful if a pre-existing over-privileged profile is
  re-validated on next use; that is wired here through
  ``authorized_profile``.

Authority boundary, stated once
-------------------------------
A bot may create, clone, re-scope and archive OTHER bots. It may not:

* widen its own authority - every grant goes through ``enforce_grant`` with the
  ACTOR's grant as the creator bound, so a bot cannot mint authority it lacks;
* edit the ceiling - ``AuthorityCeiling`` is ``frozen=True, slots=True`` and this
  module has no ceiling writer;
* edit a component that ENFORCES the ceiling - ``assert_may_modify`` refuses
  every name in ``PROTECTED_COMPONENTS``;
* exceed the live population bound - ``assert_population_within_ceiling``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.bots.authority_ceiling import (
    AuthorityCeiling,
    AuthorityViolation,
    enforce_grant,
    is_protected_component,
    narrow_to_ceiling,
)
from alpha.bots.cloning import CloneMode, get_bot_clone_engine
from alpha.bots.dynamic_profiles import (
    ApprovalGate,
    DynamicProfileStore,
    ProfileProposal,
)
from alpha.bots.lifecycle_governor import LifecycleGovernor, RetirementError
from alpha.bots.registry import BotRegistry
from alpha.channels import ledger as channel_ledger

logger = logging.getLogger(__name__)

#: Operator-level actors that are enforcement machinery rather than retirable
#: profiles. Closed set, so this cannot be used to refuse an ordinary profile.
PROTECTED_ACTORS: frozenset[str] = frozenset(
    {"alpha", "lead", "system", "server", "kill_switch", "governance", "supervisor"}
)


class LifecycleRefused(RuntimeError):
    """A lifecycle operation was refused. The refusal is on the ledger."""


class LedgerRefused(LifecycleRefused):
    """The ledger could not record the action, so the action did not happen."""


# ---------------------------------------------------------------------------
# lineage
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str
    before: Any
    after: Any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CloneLineage:
    """Who this profile came from, when, and exactly what changed.

    ``bots/cloning.py`` records the parent and a mode but no timestamp and no
    diff, which makes "what did the clone change" unanswerable after the fact.
    This is the record that answers it.
    """

    child: str
    parent: str
    clone_mode: str
    created_at: str
    created_by: str
    changes: tuple[FieldChange, ...] = ()
    #: Capabilities the parent holds that the child did NOT get, and why.
    authority_not_inherited: tuple[str, ...] = ()
    generation: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "child": self.child,
            "parent": self.parent,
            "clone_mode": self.clone_mode,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "changes": [c.to_dict() for c in self.changes],
            "authority_not_inherited": list(self.authority_not_inherited),
            "generation": self.generation,
        }

    def human_line(self) -> str:
        diff = ", ".join(f"{c.field}: {c.before!r} -> {c.after!r}" for c in self.changes) or "no field changes"
        dropped = (
            f"; not inherited: {','.join(self.authority_not_inherited)}" if self.authority_not_inherited else ""
        )
        return (
            f"{self.child} cloned from {self.parent} at {self.created_at} by {self.created_by} "
            f"({self.clone_mode}, generation {self.generation}): {diff}{dropped}"
        )


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class LifecycleResult:
    """Every lifecycle operation returns one of these, refused or not."""

    operation: str
    ok: bool
    subject: str
    actor: str
    reason: str = ""
    profile: dict[str, Any] | None = None
    lineage: dict[str, Any] | None = None
    removed_capabilities: tuple[str, ...] = ()
    transcript_retained: bool = False
    reversible_as: str = ""
    ledger_seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "ok": self.ok,
            "subject": self.subject,
            "actor": self.actor,
            "reason": self.reason,
            "profile": self.profile,
            "lineage": self.lineage,
            "removed_capabilities": list(self.removed_capabilities),
            "transcript_retained": self.transcript_retained,
            "reversible_as": self.reversible_as,
            "ledger_seq": self.ledger_seq,
        }


# ---------------------------------------------------------------------------
# the facade
# ---------------------------------------------------------------------------
class BotLifecycle:
    """Create, clone, re-scope and archive bots under a non-negotiable ceiling.

    Every mutating method follows the same order, and the order is the safety
    property:

    1. check the ceiling's protected-component list;
    2. check the actor's own authority with ``enforce_grant``;
    3. perform the operation against the existing store/governor;
    4. record it in the ledger, requiring a durability read-back;
    5. if step 4 fails, RAISE. The caller learns the operation is not trusted,
       and the partial state is reported by the exception, not hidden.
    """

    def __init__(
        self,
        store: DynamicProfileStore,
        *,
        governor: LifecycleGovernor | None = None,
        registry: BotRegistry | None = None,
        approval_gate: ApprovalGate | None = None,
        ledger_store: Any | None = None,
        ceiling: AuthorityCeiling | None = None,
        transcript_root: str | Path | None = None,
    ) -> None:
        self.store = store
        # The governor is given the SAME event store as the ledger bridge, so
        # its own ledger writes (idle-retirement proposals, status transitions)
        # land in the one ordered log this facade reads back. Left unset it
        # would fall back to the process-global store, splitting the record.
        self.governor = governor or LifecycleGovernor(store, event_store=ledger_store)
        self.registry = registry
        self.ledger_store = ledger_store
        self.transcript_root = Path(transcript_root) if transcript_root else None
        if approval_gate is not None:
            store.set_approval_gate(approval_gate)
        if ceiling is not None:
            store.set_ceiling(ceiling)

    # -- guards -----------------------------------------------------------
    def _guard_target(self, target: str, *, actor: str, reason: str) -> None:
        """Refuse any attempt to touch the enforcement machinery itself.

        ``PROTECTED_COMPONENTS`` already covers the ceiling, the store, the
        governor, the permissions gate, the ledger and the kill switch, matched
        by path segment. The extra name check below covers the operator-level
        ACTORS that are not files at all: a profile named after the leader or the
        kill switch is an attempt to retire the enforcement fleet by calling it
        something else.
        """
        if is_protected_component(target):
            self._refuse(
                actor=actor,
                target=target,
                reason=f"refused: {target} enforces the authority ceiling and no actor may modify it",
                violations=[f"protected_component:{target}"],
            )
        if (target or "").strip().lower() in PROTECTED_ACTORS:
            self._refuse(
                actor=actor,
                target=target,
                reason=(
                    f"refused: {target!r} is an enforcement actor, not a retirable profile. "
                    f"No actor may archive or re-scope the machinery that applies the ceiling."
                ),
                violations=[f"protected_actor:{target}"],
            )

    def _refuse(
        self,
        *,
        actor: str,
        target: str,
        reason: str,
        violations: Sequence[str] = (),
    ) -> None:
        """Record the refusal in the instance's ledger, then refuse.

        Uses ``self.ledger_store`` so a refusal lands in the same ordered log as
        the operations it refuses, rather than in whatever global store happens
        to exist. A refusal that cannot be recorded is still raised: the caller
        must learn the operation did not happen.
        """
        try:
            channel_ledger.note_authority_refusal(
                actor=actor,
                target=target,
                reason=reason,
                violations=list(violations),
                store=self.ledger_store,
            )
        except Exception:  # noqa: BLE001 - the refusal is the message
            logger.error("ledger unavailable while recording a lifecycle refusal for %s", target)
        raise LifecycleRefused(reason)

    def _actor_grant(self, actor_grant: Sequence[str] | str | None) -> frozenset[str]:
        """The authority the actor brings to the operation.

        A grant of ``None`` means the actor asserted nothing, so it is treated
        as the EMPTY grant. That is the fail-closed reading: an actor that did
        not state its authority cannot pass any of it on.
        """
        if actor_grant is None:
            return frozenset()
        if isinstance(actor_grant, str):
            return frozenset({actor_grant})
        return frozenset(str(c) for c in actor_grant if str(c).strip())

    # -- CREATE -----------------------------------------------------------
    def create_profile(
        self,
        name: str,
        *,
        actor: str,
        role: str = "",
        system_prompt: str = "",
        description: str = "",
        capabilities: Sequence[str] = (),
        skills: Sequence[str] = (),
        mcp_servers: Sequence[str] = (),
        actor_grant: Sequence[str] | None = None,
        model: str | None = None,
        department: str = "engineering",
        rationale: str = "",
        enable: bool = True,
    ) -> LifecycleResult:
        """Create a specialised profile at runtime.

        The grant is recomputed by the store from the ACTOR's grant and the
        ceiling; nothing on the proposal is trusted as an authorisation.
        """
        self._guard_target(name, actor=actor, reason="create profile")
        grant = self._actor_grant(actor_grant)
        proposal = ProfileProposal(
            profile_name=name,
            requested_capabilities=list(capabilities),
            role=role,
            system_prompt=system_prompt,
            description=description,
            skills=list(skills),
            mcp_servers=list(mcp_servers),
            model=model,
            department=department,
            creator=actor,
            creator_grant=sorted(grant),
            rationale=rationale or f"specialist profile {name!r} requested by @{actor}",
        )
        try:
            self.store.propose_profile(proposal)
            self.store.approve_proposal(name)
            profile = self.store.install_approved(name)
        except AuthorityViolation as exc:
            self._refuse(
                actor=actor,
                target=name,
                reason=f"create refused: {exc}",
                violations=exc.violations,
            )
            raise  # unreachable; keeps type checkers honest
        if enable:
            try:
                profile = self.governor.enable_profile(name, actor=actor, reason="profile created and enabled")
            except RetirementError as exc:
                return LifecycleResult(
                    operation="create",
                    ok=False,
                    subject=name,
                    actor=actor,
                    reason=f"created but not enabled: {exc}",
                    profile=profile.to_dict(),
                )
        return LifecycleResult(
            operation="create",
            ok=True,
            subject=name,
            actor=actor,
            reason="profile created",
            profile=profile.to_dict(),
        )

    # -- CLONE ------------------------------------------------------------
    def clone_profile(
        self,
        source: str,
        target: str,
        *,
        actor: str,
        actor_grant: Sequence[str] | None = None,
        clone_mode: str = "specialist_fork",
        specialist_directive: str | None = None,
        skills_to_add: Sequence[str] = (),
        tools_to_add: Sequence[str] = (),
        enable: bool = True,
        rationale: str = "",
        on_excess: str = "refuse",
    ) -> LifecycleResult:
        """Clone an existing bot, then modify the clone.

        Two independent authority checks, and the second is the one that matters:

        * ``enforce_grant`` bounds the clone by the ACTOR's grant and the
          ceiling. A clone can never hold authority its creator lacks, even if
          the PARENT holds more than the creator.
        * The parent's own grant is recorded as ``authority_not_inherited`` so
          the gap is on the record rather than merely enforced.

        The registry-level copy is done by the existing
        :class:`alpha.bots.cloning.BotCloneEngine` when a registry is available;
        the profile-level record is always made, because that is where the
        ceiling is enforced.

        ``on_excess`` chooses what happens when the parent holds more than the
        creator may pass on:

        * ``"refuse"`` (the default, and what ``dynamic_profiles`` already does
          at creation time) raises. The operator sees the over-privileged
          request rather than a quietly lesser clone.
        * ``"narrow"`` clones with the intersection and records the dropped
          capabilities in ``authority_not_inherited``, so the gap is explicit
          on the lineage. It still never narrows past the CREATOR's grant.
        """
        self._guard_target(target, actor=actor, reason="clone profile")
        if on_excess not in {"refuse", "narrow"}:
            raise ValueError(f"on_excess must be 'refuse' or 'narrow', got {on_excess!r}")
        parent = self.store.get_profile(source)
        if parent is None:
            self._refuse(actor=actor, target=source, reason=f"clone refused: no profile named {source!r}")

        grant = self._actor_grant(actor_grant)
        # What the clone ASKS for: everything the parent declares, plus additions.
        requested = sorted(set(parent.declared_capabilities) | set(parent.granted_capabilities) | set(skills_to_add))
        try:
            # Refuse (default), never silently narrow: a clone that would exceed
            # the creator is an error the operator must see.
            granted = enforce_grant(
                requested,
                creator_grant=grant,
                subject=f"clone {target!r} of {source!r}",
                ceiling=self.store.ceiling(),
            )
        except AuthorityViolation as exc:
            if on_excess != "narrow":
                self._refuse(
                    actor=actor,
                    target=target,
                    reason=f"clone refused: {exc}",
                    violations=exc.violations,
                )
                raise  # unreachable
            within_ceiling, _dropped = narrow_to_ceiling(
                requested,
                subject=f"clone {target!r} of {source!r}",
                ceiling=self.store.ceiling(),
            )
            # Narrow to what the creator can actually pass on. The creator bound is
            # the intersection, never a superset: narrowing past the creator is
            # the one thing this branch must never do.
            granted = enforce_grant(
                sorted(set(within_ceiling) & set(grant)),
                creator_grant=sorted(grant),
                subject=f"clone {target!r} of {source!r} (narrowed to creator authority)",
                ceiling=self.store.ceiling(),
            )

        not_inherited = tuple(sorted(set(parent.granted_capabilities) - set(granted)))

        prior_generation = int((parent.metadata or {}).get("generation", 1) or 1)
        system_prompt = parent.system_prompt
        if specialist_directive:
            system_prompt = f"{system_prompt}\n\n### SPECIALIST MISSION DIRECTIVE ({target})\n{specialist_directive}\n"
        changes = (
            FieldChange("name", parent.name, target),
            FieldChange("system_prompt", parent.system_prompt, system_prompt),
            FieldChange(
                "capabilities",
                sorted(parent.granted_capabilities),
                sorted(granted),
            ),
            FieldChange("skills", list(parent.skills), list(dict.fromkeys([*parent.skills, *skills_to_add]))),
            FieldChange("creator", parent.creator, actor),
        )
        lineage = CloneLineage(
            child=target,
            parent=parent.name,
            clone_mode=clone_mode,
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            created_by=actor,
            changes=changes,
            authority_not_inherited=not_inherited,
            generation=prior_generation + 1,
        )

        registry_lineage: dict[str, Any] = {}
        if self.registry is not None:
            try:
                engine = get_bot_clone_engine(self.registry)
                cloned = engine.clone_bot(
                    source_name=parent.name,
                    target_name=target,
                    mode=CloneMode(clone_mode),
                    specialist_directive=specialist_directive,
                    skills_to_add=list(skills_to_add),
                    tools_to_add=list(tools_to_add),
                    ttl_seconds=0,
                )
                registry_lineage = {
                    "registry_clone": cloned.name,
                    "engine_metadata": {
                        k: v for k, v in (cloned.metadata or {}).items() if k in {"cloned_from", "clone_mode", "lineage"}
                    },
                }
            except Exception as exc:  # noqa: BLE001
                # The registry copy is best-effort; the ceiling-enforced profile
                # is the authoritative artefact, and a registry failure must not
                # be reported as a successful clone of it.
                registry_lineage = {"registry_clone_failed": f"{type(exc).__name__}: {exc}"}

        result = self.create_profile(
            target,
            actor=actor,
            role=f"{parent.role} [Specialist]" if parent.role else "Specialist",
            system_prompt=system_prompt,
            description=f"Clone of {parent.name}: {parent.description}".strip(),
            capabilities=sorted(granted),
            skills=list(dict.fromkeys([*parent.skills, *skills_to_add])),
            mcp_servers=list(parent.mcp_servers),
            actor_grant=sorted(grant),
            department=parent.department,
            rationale=rationale or lineage.human_line(),
            enable=enable,
        )
        if not result.ok:
            return result

        # Attach the lineage to the created profile. Done after creation so the
        # lineage describes what actually happened rather than what was planned.
        profile = self.store.get_profile(target)
        if profile is not None:
            metadata = dict(profile.metadata or {})
            metadata["lineage"] = lineage.to_dict()
            metadata["generation"] = lineage.generation
            metadata["cloned_from"] = parent.name
            metadata["clone_mode"] = clone_mode
            metadata.update(registry_lineage)
            profile.metadata = metadata
            self.store._save()

        entry = channel_ledger.append(
            channel_ledger.EV_TOOL_MODIFIED,
            actor=actor,
            target=target,
            reason=f"profile {target!r} cloned from {parent.name!r}",
            details={"lineage": lineage.to_dict(), **registry_lineage},
            store=self.ledger_store,
        )
        result.lineage = lineage.to_dict()
        result.ledger_seq = entry.seq
        result.reason = lineage.human_line()
        return result

    # -- RE-SCOPE ---------------------------------------------------------
    def rescope_profile(
        self,
        name: str,
        *,
        actor: str,
        new_capabilities: Sequence[str],
        reason: str = "",
        actor_grant: Sequence[str] | None = None,
    ) -> LifecycleResult:
        """Change a live profile's grant.

        In-flight work is not silently continued: the governor bumps
        ``profile_version`` and records the version transition, so any dispatch
        that has not re-validated is provably on a superseded grant. A widening
        is bounded by ``min(ceiling, creator grant)`` exactly as creation is.
        """
        self._guard_target(name, actor=actor, reason="re-scope profile")
        # The ACTOR's grant is deliberately not passed down here. The governor
        # bounds a re-scope by the profile's own creator_grant, which is the
        # authority the profile was born with. Using the actor's grant instead
        # would let a well-credited actor widen a profile beyond what its
        # creator could have granted, which is exactly the hole re-scoping must
        # not be.
        before = self.store.get_profile(name)
        previous = sorted(before.granted_capabilities) if before else []
        try:
            profile = self.governor.rescope_profile(
                name,
                new_capabilities=list(new_capabilities),
                actor=actor,
                reason=reason or f"re-scoped by @{actor}",
            )
        except AuthorityViolation as exc:
            self._refuse(
                actor=actor, target=name, reason=f"re-scope refused: {exc}", violations=exc.violations
            )
            raise  # unreachable
        # Fail closed on the ledger: a re-scope nobody can audit is not a
        # completed re-scope, so the exception propagates to the caller.
        seq = self._record_rescope(
            name,
            actor=actor,
            reason=reason or f"re-scoped by @{actor}",
            previous=previous,
            new=sorted(profile.granted_capabilities),
        )
        return LifecycleResult(
            operation="rescope",
            ok=True,
            subject=name,
            actor=actor,
            reason=reason or f"re-scoped by @{actor}",
            profile=profile.to_dict(),
            removed_capabilities=tuple(sorted(set(previous) - set(profile.granted_capabilities))),
            ledger_seq=seq,
        )

    # -- ARCHIVE (never delete) -------------------------------------------
    def archive_profile(
        self,
        name: str,
        *,
        actor: str,
        reason: str = "",
        drain_deadline_seconds: float = 0.0,
    ) -> LifecycleResult:
        """Retire a bot: stop dispatch, drain in flight, PRESERVE everything.

        Never deletes. The profile record, its grant, its history counters and
        its chat transcript all remain readable afterwards, and
        :meth:`unarchive_profile` puts it back. A hard delete of a bot with
        history would be a defect, so there is no delete method.
        """
        self._guard_target(name, actor=actor, reason="archive profile")
        before = self.store.get_profile(name)
        if before is None:
            self._refuse(actor=actor, target=name, reason=f"archive refused: no profile named {name!r}")
        retirement = self.governor.retire_profile(
            name,
            actor=actor,
            reason=reason or f"archived by @{actor}",
            drain_deadline_seconds=drain_deadline_seconds,
        )
        transcript = self._archive_transcript(name)
        after = self.store.get_profile(name)
        # Recorded under the governance action the retirement already used, so
        # the archive is the SAME ledger entry an auditor reads for a retire,
        # carrying the extra facts the governor does not know about: that the
        # transcript was retained and that the archive is reversible.
        from alpha.bots.governance_ledger import ACTION_PROFILE_RETIRED

        entry = channel_ledger.governance(
            ACTION_PROFILE_RETIRED,
            actor=actor,
            target=name,
            reason=reason or f"archived by @{actor} (never deleted)",
            details={
                "operation": "archive",
                "previous_status": retirement.previous_status,
                "status": retirement.status,
                "in_flight_at_retire": retirement.in_flight_at_retire,
                "abandoned": [c.to_dict() for c in retirement.abandoned],
                "audit_record_retained": True,
                "transcript_retained": transcript is not None,
                "reversible_as": "unarchive_profile",
            },
            store=self.ledger_store,
        )
        return LifecycleResult(
            operation="archive",
            ok=True,
            subject=name,
            actor=actor,
            reason=reason or f"archived by @{actor}",
            profile=after.to_dict() if after else None,
            transcript_retained=True,
            reversible_as="unarchive_profile",
            ledger_seq=entry.seq,
        )

    def unarchive_profile(self, name: str, *, actor: str, reason: str = "") -> LifecycleResult:
        """Reverse an archive. The audit record is untouched by this."""
        self._guard_target(name, actor=actor, reason="unarchive profile")
        profile = self.store.get_profile(name)
        if profile is None:
            self._refuse(actor=actor, target=name, reason=f"unarchive refused: no profile named {name!r}")
        try:
            restored = self.governor.enable_profile(
                name, actor=actor, reason=reason or f"unarchived by @{actor}"
            )
        except RetirementError as exc:
            return LifecycleResult(
                operation="unarchive",
                ok=False,
                subject=name,
                actor=actor,
                reason=f"cannot unarchive: {exc}",
                profile=profile.to_dict(),
            )
        return LifecycleResult(
            operation="unarchive",
            ok=True,
            subject=name,
            actor=actor,
            reason=reason or f"unarchived by @{actor}",
            profile=restored.to_dict(),
            transcript_retained=True,
        )

    def _record_rescope(
        self, name: str, *, actor: str, reason: str, previous: Sequence[str], new: Sequence[str]
    ) -> int | None:
        """Record a re-scope in the one ordered ledger.

        ``lifecycle_governor`` already writes ``ACTION_PROFILE_RESCOPED`` for its
        own status transitions. This adds the CAPABILITY change with the
        before/after, which is the part an auditor needs and the part the
        governor's details do not carry for a re-scope.
        """
        from alpha.bots.governance_ledger import ACTION_PROFILE_RESCOPED

        entry = channel_ledger.governance(
            ACTION_PROFILE_RESCOPED,
            actor=actor,
            target=name,
            reason=reason or f"profile {name!r} re-scoped by @{actor}",
            details={
                "operation": "rescope",
                "previous_capabilities": sorted(previous),
                "new_capabilities": sorted(new),
            },
            store=self.ledger_store,
        )
        return entry.seq

    def _archive_transcript(self, name: str) -> Path | None:
        """Record that the transcript is retained. Nothing is moved or deleted.

        The marker is written INSIDE the bot's own transcript directory, so a
        single recursive read recovers both the marker and every run transcript
        beneath it. Writing it to a separate archive tree would be a second copy
        of the record, and a second copy is a second thing to lose.
        """
        if self.transcript_root is None:
            return None
        room = self.transcript_root / name
        room.mkdir(parents=True, exist_ok=True)
        profile = self.store.get_profile(name)
        marker = room / "archive_record.jsonl"
        marker.write_text(
            json.dumps(
                {
                    # Deliberately NO "seq": the reader uses that key to
                    # recognise transcript entries, and an archive marker is
                    # not a transcript message. It would otherwise be counted
                    # as one and corrupt the recovered sequence.
                    "record": "archive",
                    "archived": True,
                    "profile": name,
                    "status": "retired",
                    "note": "transcript retained in full; archiving is reversible",
                    "granted_capabilities": sorted(profile.granted_capabilities) if profile else [],
                },
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return marker

    def read_archived_transcript(self, name: str) -> list[dict[str, Any]]:
        """Read a retired bot's transcript back. Proof that archiving preserved it.

        Recursive, because a war room writes ``<room>/<run_id>/transcript.jsonl``
        and a bot may hold several rooms and several runs. Every run is read, so
        the recovered sequence is the merge of all of them in order.
        """
        if self.transcript_root is None:
            return []
        room = self.transcript_root / name
        messages: list[dict[str, Any]] = []
        for path in sorted(room.rglob("*.jsonl")):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                record["_source"] = path.name
                messages.append(record)
        messages.sort(key=lambda m: m.get("seq", 0))
        return messages

    # -- ceiling tightening ----------------------------------------------
    def revalidate(self, name: str) -> LifecycleResult:
        """Re-check one profile against the CURRENT ceiling.

        This is what makes a tightening bite. Without it, lowering
        ``max_capability_rank`` does nothing to profiles that already exist.
        """
        profile, removed = self.store.revalidate_profile(name)
        if profile is None:
            return LifecycleResult(
                operation="revalidate", ok=False, subject=name, actor="server", reason="no such profile"
            )
        return LifecycleResult(
            operation="revalidate",
            ok=True,
            subject=name,
            actor="server",
            reason="re-validated against the current ceiling",
            profile=profile.to_dict(),
            removed_capabilities=tuple(removed),
        )

    def revalidate_all(self) -> list[LifecycleResult]:
        """Re-validate every profile. Run after a ceiling change."""
        return [self.revalidate(p.name) for p in self.store.list_profiles()]

    # -- idle retirement proposals ---------------------------------------
    def propose_idle_retirements(
        self, *, actor: str = "alpha", window_seconds: float = 86400.0, reason: str = ""
    ) -> list[dict[str, Any]]:
        """Idle profiles are PROPOSED for retirement, never auto-killed.

        Delegates to the existing :meth:`LifecycleGovernor.propose_idle_retirements`,
        which writes an ``ACTION_PROFILE_IDLE_PROPOSED`` ledger entry and returns
        proposals that still have to traverse the approval path.
        """
        proposals = self.governor.propose_idle_retirements(
            window_seconds=window_seconds, actor=actor, reason=reason
        )
        return [p.to_dict() for p in proposals]

    # -- population -------------------------------------------------------
    def live_population(self) -> int:
        return self.store.live_profile_count()

    def assert_population_within_ceiling(self) -> None:
        self.store.ceiling().assert_population_within_ceiling(
            live=self.store.live_profile_count(),
            total=self.store.total_profile_count(),
            subject="war-room lifecycle",
        )
