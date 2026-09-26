"""Default-deny audience authorization for social/shared facts.

Ownership is the only implicit read authority. Every cross-scope read requires
an exact, active, audited ``AudienceGrant``; team labels, audience labels, and
scope-name similarity grant nothing. Every allow/deny result includes a reason,
and every allowed cross-scope result carries the grant used for disclosure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .config import SocialConfig
from .models import (
    AccessDecision,
    AudienceGrant,
    SharedFact,
    VisibleFacts,
    normalize_scope,
)
from .provenance import SocialProvenance, SocialProvenanceError
from .store import ScopeState, SocialStore, SocialStoreCapacityError


class GrantExpiryAuditError(SocialProvenanceError):
    """Expiry fail-closed error that discloses how many state markers were saved."""

    def __init__(self, message: str, *, expired_count: int) -> None:
        super().__init__(message)
        self.expired_count = expired_count


@dataclass(frozen=True, slots=True)
class GrantResult:
    success: bool
    reason: str
    message: str
    grant: AudienceGrant | None = None


class SharingManager:
    """Permission core for owner, fact, and scope grants."""

    def __init__(
        self,
        store: SocialStore,
        config: SocialConfig,
        provenance: SocialProvenance,
    ) -> None:
        self.store = store
        self.config = config
        self.provenance = provenance

    @staticmethod
    def _decision(
        fact: SharedFact,
        reader_scope: str,
        *,
        allowed: bool,
        reason: str,
        message: str,
        grant: AudienceGrant | None = None,
    ) -> AccessDecision:
        return AccessDecision(
            fact_id=fact.id,
            owner_scope=fact.owner_scope,
            reader_scope=reader_scope,
            allowed=allowed,
            reason=reason,
            message=message,
            cross_scope=reader_scope != fact.owner_scope,
            grant=grant.model_copy(deep=True) if grant is not None else None,
        )

    def can_read(
        self,
        fact: SharedFact,
        reader_scope: str,
        *,
        now: float | None = None,
    ) -> AccessDecision:
        """Authorize one canonical fact read without treating labels as grants."""

        at = time.time() if now is None else float(now)
        reader = normalize_scope(reader_scope)
        owner = normalize_scope(fact.owner_scope)
        cross_scope = reader != owner
        snapshot = self.store.snapshot(owner)
        canonical = next((item for item in snapshot.facts if item.id == fact.id), None)
        if canonical is None:
            return self._decision(
                fact,
                reader,
                allowed=False,
                reason="fact_not_found",
                message="The fact is not present in its owner scope; access is denied.",
            )
        if canonical.is_expired(at):
            return self._decision(
                canonical,
                reader,
                allowed=False,
                reason="fact_expired",
                message="The fact expired; access is denied.",
            )
        if not cross_scope:
            return self._decision(
                canonical,
                reader,
                allowed=True,
                reason="owner_scope",
                message="The reader owns this fact.",
            )
        if not self.config.require_grant_for_team_scope:
            return self._decision(
                canonical,
                reader,
                allowed=False,
                reason="grant_requirement_disabled",
                message="Cross-scope grants cannot be disabled; access is denied.",
            )

        candidates = [grant for grant in snapshot.grants if grant.grantee_scope == reader and grant.applies_to(canonical.id)]
        active = [grant for grant in candidates if grant.is_active(at)]
        fact_specific = [grant for grant in active if grant.fact_id == canonical.id]
        if canonical.sensitivity == "private" and fact_specific:
            selected = max(fact_specific, key=lambda grant: (grant.granted_at, grant.id))
            return self._decision(
                canonical,
                reader,
                allowed=True,
                reason="active_fact_grant",
                message="An active fact-specific audience grant permits this read.",
                grant=selected,
            )
        if canonical.sensitivity != "private" and active:
            selected = max(
                active,
                key=lambda grant: (grant.fact_id == canonical.id, grant.granted_at, grant.id),
            )
            return self._decision(
                canonical,
                reader,
                allowed=True,
                reason="active_audience_grant",
                message="An active audience grant permits this cross-scope read.",
                grant=selected,
            )

        revoked = [grant for grant in candidates if grant.revoked_at is not None]
        expired = [grant for grant in candidates if grant.revoked_at is None and (grant.expired_at is not None or (grant.expires_at is not None and grant.expires_at <= at))]
        if revoked:
            reason = "grant_revoked"
            message = "The only matching audience grant was revoked; access is denied."
        elif expired:
            reason = "grant_expired"
            message = "The only matching audience grant expired; access is denied."
        elif canonical.sensitivity == "private":
            reason = "private_fact_requires_explicit_grant"
            message = "A private fact has no active fact-specific grant; access is denied."
        else:
            reason = "active_audience_grant_required"
            message = "No active audience grant matches this reader; access is denied."
        return self._decision(canonical, reader, allowed=False, reason=reason, message=message)

    def grant(
        self,
        owner_scope: str,
        grantee_scope: str,
        *,
        granted_by: str,
        fact_id: str | None = None,
        expires_at: float | None = None,
        reason: str = "explicit audience grant",
        now: float | None = None,
    ) -> GrantResult:
        """Create and audit a fact-specific or owner-scope grant."""

        at = time.time() if now is None else float(now)
        owner = normalize_scope(owner_scope)
        grantee = normalize_scope(grantee_scope)
        granter = normalize_scope(granted_by)
        grant_reason = str(reason or "").strip()
        if not self.config.require_grant_for_team_scope:
            return GrantResult(False, "grant_requirement_disabled", "Explicit grants cannot be disabled.")
        if granter != owner:
            return GrantResult(False, "grantor_must_be_owner", "Only the fact owner may grant audience access.")
        if grantee == owner:
            return GrantResult(False, "owner_needs_no_grant", "The owner already has implicit access.")
        if not grant_reason:
            return GrantResult(False, "grant_reason_required", "Audience grants require an audit reason.")
        if expires_at is not None and float(expires_at) <= at:
            return GrantResult(False, "grant_expiry_must_be_future", "Audience grants must expire in the future.")
        if fact_id is not None:
            fact = self.store.get_fact(owner, fact_id)
            if fact is None:
                return GrantResult(False, "fact_not_found", "The fact does not exist in the owner scope.")
        elif any(item.sensitivity == "private" for item in self.store.list_facts(owner)):
            return GrantResult(
                False,
                "private_facts_require_specific_grants",
                "A scope grant is refused when the owner scope contains private facts; grant each fact explicitly.",
            )

        grant = AudienceGrant(
            owner_scope=owner,
            fact_id=fact_id,
            grantee_scope=grantee,
            granted_by=granter,
            granted_at=at,
            expires_at=expires_at,
            reason=grant_reason,
        )
        try:
            stored = self.store.put_grant(grant, now=at)
        except SocialStoreCapacityError as exc:
            return GrantResult(False, "grant_capacity_exceeded", str(exc))
        try:
            self.provenance.append(
                "grant",
                owner_scope=owner,
                actor=granter,
                target=grantee,
                reason=grant_reason,
                details={"grant_id": stored.id, "target": stored.target},
                now=at,
            )
        except SocialProvenanceError:
            self.store.remove_grants(owner, {stored.id})
            raise
        return GrantResult(True, "granted", "Audience access was granted and audited.", stored)

    def revoke(
        self,
        owner_scope: str,
        grant_id: str,
        *,
        revoked_by: str,
        reason: str,
        now: float | None = None,
    ) -> GrantResult:
        """Revoke access immediately and append the revocation before returning."""

        at = time.time() if now is None else float(now)
        owner = normalize_scope(owner_scope)
        actor = normalize_scope(revoked_by)
        revoke_reason = str(reason or "").strip()
        if actor != owner:
            return GrantResult(False, "revoker_must_be_owner", "Only the owner may revoke audience access.")
        if not revoke_reason:
            return GrantResult(False, "revoke_reason_required", "Revocation requires an audit reason.")
        current = self.store.get_grant(owner, grant_id)
        if current is None:
            return GrantResult(False, "grant_not_found", "The audience grant does not exist.")
        if current.revoked_at is not None:
            return GrantResult(False, "grant_already_revoked", "The audience grant was already revoked.", current)
        revoked = current.model_copy(deep=True, update={"revoked_at": at})
        self.store.put_grant(revoked, now=at)
        try:
            self.provenance.append(
                "revocation",
                owner_scope=owner,
                actor=actor,
                target=current.grantee_scope,
                reason=revoke_reason,
                details={"grant_id": current.id, "target": current.target},
                now=at,
            )
        except SocialProvenanceError:
            self.store.put_grant(current, now=at)
            raise
        return GrantResult(True, "revoked", "Audience access was revoked and audited.", revoked)

    def expire_grants(self, *, now: float | None = None) -> int:
        """Mark all due grants expired and audit each state transition."""

        at = time.time() if now is None else float(now)
        changed: list[tuple[str, AudienceGrant]] = []

        def expire(scope: str) -> None:
            def update(doc: ScopeState) -> None:
                for grant in list(doc.grants.values()):
                    due = grant.expires_at is not None and grant.expires_at <= at
                    if due and grant.expired_at is None:
                        expired = grant.model_copy(deep=True, update={"expired_at": at})
                        doc.grants[grant.id] = expired
                        changed.append((scope, expired.model_copy(deep=True)))

            self.store.update_scope(scope, update, now=at)

        for scope in self.store.list_scope_ids():
            expire(scope)
        for scope, grant in changed:
            try:
                self.provenance.append(
                    "grant_expiry",
                    owner_scope=scope,
                    actor="system",
                    target=grant.grantee_scope,
                    reason="scheduled audience grant expiry",
                    details={"grant_id": grant.id, "target": grant.target, "expires_at": grant.expires_at},
                    now=at,
                )
            except SocialProvenanceError as exc:
                raise GrantExpiryAuditError(
                    f"{exc}; {len(changed)} grant expiry marker(s) were saved before audit failure",
                    expired_count=len(changed),
                ) from exc
        return len(changed)

    def visible_facts(
        self,
        reader_scope: str,
        *,
        now: float | None = None,
    ) -> VisibleFacts:
        """Return only authorized fact bodies plus reasoned allow/deny decisions."""

        at = time.time() if now is None else float(now)
        reader = normalize_scope(reader_scope)
        scopes = set(self.store.list_scope_ids())
        scopes.add(reader)
        facts: list[SharedFact] = []
        decisions: list[AccessDecision] = []
        for owner_scope in sorted(scopes):
            snapshot = self.store.snapshot(owner_scope)
            for fact in sorted(snapshot.facts, key=lambda item: item.id):
                decision = self.can_read(fact, reader, now=at)
                decisions.append(decision)
                if decision.allowed:
                    facts.append(fact.model_copy(deep=True))
        if facts:
            status = "ok"
            reason = f"{len(facts)} authorized fact(s)"
        elif decisions:
            status = "empty"
            reason = "no facts passed the default-deny audience policy"
        else:
            status = "empty"
            reason = "no social facts are stored"
        return VisibleFacts(
            reader_scope=reader,
            status=status,
            reason=reason,
            facts=facts,
            decisions=decisions,
        )


__all__ = ["GrantExpiryAuditError", "GrantResult", "SharingManager"]
