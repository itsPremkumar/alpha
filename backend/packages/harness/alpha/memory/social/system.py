"""High-level, default-off facade for social/shared memory.

The facade is the only convenience surface that applies both the host master
switch and ``SocialConfig.enabled``. Lower-level stores and permission checks
remain independently usable, but cross-scope reads still pass through
``SharingManager`` and its explicit-grant policy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from .config import SocialConfig
from .models import Counterpart, InteractionSummary, SharedFact, VisibleFacts
from .provenance import SocialProvenance, SocialProvenanceError
from .recall import RecallBlock, SocialStats, relationship_block, shared_context
from .recall import stats as build_stats
from .relationships import RelationshipManager, RelationshipUpdate
from .sharing import GrantResult, SharingManager
from .store import SocialStore
from .summary import SummaryModel, summarize_interaction


@dataclass(frozen=True, slots=True)
class CounterpartWriteResult:
    success: bool
    reason: str
    message: str
    counterpart: Counterpart | None = None


@dataclass(frozen=True, slots=True)
class FactWriteResult:
    success: bool
    reason: str
    message: str
    fact: SharedFact | None = None


@dataclass(frozen=True, slots=True)
class InteractionWriteResult:
    success: bool
    state_saved: bool
    reason: str
    message: str
    update: RelationshipUpdate | None = None
    audit_status: Literal["written", "failed", "not_attempted"] = "not_attempted"


@dataclass(frozen=True, slots=True)
class MergeWriteResult:
    success: bool
    state_saved: bool
    reason: str
    message: str
    counterpart: Counterpart | None = None
    audit_status: Literal["written", "failed", "not_attempted"] = "not_attempted"


@dataclass(frozen=True, slots=True)
class ExpiryResult:
    success: bool
    reason: str
    message: str
    expired_count: int = 0


class SocialMemorySystem:
    """Owner-scoped social memory orchestration with no process-global singleton."""

    def __init__(
        self,
        *,
        config: SocialConfig | None = None,
        store: SocialStore | None = None,
        provenance: SocialProvenance | None = None,
        summary_model: SummaryModel | None = None,
        master_enabled: bool = True,
    ) -> None:
        self.store = store or SocialStore(config or SocialConfig())
        self.config = config or self.store.config
        self.provenance = provenance or SocialProvenance(self.store.root)
        self.summary_model = summary_model
        self.master_enabled = bool(master_enabled)
        self.relationships = RelationshipManager(self.store, self.config)
        self.sharing = SharingManager(self.store, self.config, self.provenance)

    @property
    def enabled(self) -> bool:
        return self.master_enabled and self.config.enabled

    def _disabled_message(self) -> str:
        if not self.master_enabled:
            return "Social memory is disabled by the host memory master switch."
        return "Social memory is disabled by memory.social.enabled."

    def upsert_counterpart(
        self,
        owner_scope: str,
        counterpart: Counterpart,
        *,
        now: float | None = None,
    ) -> CounterpartWriteResult:
        if not self.enabled:
            return CounterpartWriteResult(False, "social_memory_disabled", self._disabled_message())
        try:
            stored = self.relationships.upsert_counterpart(owner_scope, counterpart, now=now)
        except (KeyError, TypeError, ValueError) as exc:
            return CounterpartWriteResult(False, "counterpart_invalid", str(exc))
        return CounterpartWriteResult(True, "counterpart_upserted", "Counterpart identity was stored.", stored)

    def record_interaction(
        self,
        owner_scope: str,
        counterpart: Counterpart | str,
        *,
        status: str = "unknown",
        outcomes: list[str] | tuple[str, ...] = (),
        topics: list[str] | tuple[str, ...] = (),
        interaction_count: int = 1,
        summary: InteractionSummary | None = None,
        now: float | None = None,
    ) -> InteractionWriteResult:
        if not self.enabled:
            return InteractionWriteResult(
                False,
                False,
                "social_memory_disabled",
                self._disabled_message(),
            )
        at = time.time() if now is None else float(now)
        if isinstance(counterpart, str):
            counterpart_record = Counterpart(
                display_name=counterpart,
                kind="user",
                first_seen=at,
                last_seen=at,
            )
        else:
            counterpart_record = counterpart.model_copy(deep=True)
        interaction_summary = summary or summarize_interaction(
            counterpart_record.id,
            status=status,
            outcomes=outcomes,
            topics=topics,
            interaction_count=interaction_count,
            model=self.summary_model,
            config=self.config,
            now=at,
        )
        try:
            update = self.relationships.record_interaction(
                owner_scope,
                counterpart_record,
                interaction_summary,
                topics=topics,
                now=at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return InteractionWriteResult(False, False, "interaction_invalid", str(exc))
        try:
            self.provenance.append(
                "interaction",
                owner_scope=owner_scope,
                actor=owner_scope,
                target=update.counterpart.id,
                reason="interaction recorded",
                details={
                    "summary_id": update.summary.id,
                    "status": update.summary.status,
                    "summary_source": update.summary.source,
                    "fallback_reason": update.summary.fallback_reason,
                    "interaction_count": update.summary.interaction_count,
                    "topics": update.summary.topics,
                },
                now=at,
            )
        except (SocialProvenanceError, ValueError) as exc:
            return InteractionWriteResult(
                False,
                True,
                "interaction_saved_but_audit_failed",
                f"Relationship state was saved, but required provenance failed: {exc}",
                update,
                "failed",
            )
        return InteractionWriteResult(
            True,
            True,
            "interaction_recorded",
            "Counterpart, relationship, summary, and provenance were recorded.",
            update,
            "written",
        )

    def merge_counterparts(
        self,
        owner_scope: str,
        source_id: str,
        target_id: str,
        *,
        reason: str,
        now: float | None = None,
    ) -> MergeWriteResult:
        if not self.enabled:
            return MergeWriteResult(False, False, "social_memory_disabled", self._disabled_message())
        at = time.time() if now is None else float(now)
        try:
            merged = self.relationships.merge_counterparts(
                owner_scope,
                source_id,
                target_id,
                reason=reason,
                now=at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return MergeWriteResult(False, False, "merge_invalid", str(exc))
        try:
            self.provenance.append(
                "merge",
                owner_scope=owner_scope,
                actor=owner_scope,
                target=target_id,
                reason=reason,
                details={"source_id": source_id, "target_id": target_id, "counterpart_id": merged.id},
                now=at,
            )
        except (SocialProvenanceError, ValueError) as exc:
            return MergeWriteResult(
                False,
                True,
                "merge_saved_but_audit_failed",
                f"Identity merge was saved, but required provenance failed: {exc}",
                merged,
                "failed",
            )
        return MergeWriteResult(
            True,
            True,
            "counterparts_merged",
            "Counterpart identities were merged and audited.",
            merged,
            "written",
        )

    def add_fact(
        self,
        owner_scope: str,
        content: str,
        *,
        created_by: str | None = None,
        audience: list[str] | tuple[str, ...] = (),
        sensitivity: str | None = None,
        fact_id: str | None = None,
        expires_at: float | None = None,
        metadata: dict | None = None,
        now: float | None = None,
    ) -> FactWriteResult:
        if not self.enabled:
            return FactWriteResult(False, "social_memory_disabled", self._disabled_message())
        at = time.time() if now is None else float(now)
        try:
            values = {
                "owner_scope": owner_scope,
                "content": content,
                "audience": list(audience),
                "sensitivity": sensitivity or self.config.default_sensitivity,
                "created_by": created_by or owner_scope,
                "created_at": at,
                "expires_at": expires_at,
                "metadata": dict(metadata or {}),
            }
            if fact_id:
                values["id"] = fact_id
            fact = SharedFact.model_validate(values)
        except (TypeError, ValueError) as exc:
            return FactWriteResult(False, "fact_invalid", str(exc))
        stored = self.store.put_fact(fact, now=at)
        return FactWriteResult(True, "fact_stored", "Owner-scoped shared fact was stored.", stored)

    def grant(self, owner_scope: str, grantee_scope: str, **kwargs) -> GrantResult:
        if not self.enabled:
            return GrantResult(False, "social_memory_disabled", self._disabled_message())
        return self.sharing.grant(owner_scope, grantee_scope, **kwargs)

    def revoke(self, owner_scope: str, grant_id: str, **kwargs) -> GrantResult:
        if not self.enabled:
            return GrantResult(False, "social_memory_disabled", self._disabled_message())
        return self.sharing.revoke(owner_scope, grant_id, **kwargs)

    def expire_grants(self, *, now: float | None = None) -> ExpiryResult:
        if not self.enabled:
            return ExpiryResult(False, "social_memory_disabled", self._disabled_message())
        try:
            count = self.sharing.expire_grants(now=now)
        except (SocialProvenanceError, ValueError) as exc:
            expired_count = int(getattr(exc, "expired_count", 0))
            return ExpiryResult(
                False,
                "grant_expiry_audit_failed",
                str(exc),
                count=expired_count,
            )
        return ExpiryResult(True, "grants_expired", f"Expired and audited {count} audience grant(s).", count)

    def visible_facts(self, reader_scope: str, *, now: float | None = None) -> VisibleFacts:
        if not self.enabled:
            return VisibleFacts(
                reader_scope=str(reader_scope or ""),
                status="disabled",
                reason="social_memory_disabled",
            )
        return self.sharing.visible_facts(reader_scope, now=now)

    def relationship_block(
        self,
        owner_scope: str,
        *,
        limit: int = 5,
        now: float | None = None,
    ) -> RecallBlock:
        return relationship_block(self.relationships, self.config, owner_scope, limit=limit, now=now)

    def shared_context(
        self,
        reader_scope: str,
        *,
        limit: int = 5,
        now: float | None = None,
    ) -> RecallBlock:
        return shared_context(self.sharing, self.config, reader_scope, limit=limit, now=now)

    def stats(self, owner_scope: str, *, now: float | None = None) -> SocialStats:
        if not self.enabled:
            return SocialStats(
                owner_scope=owner_scope,
                enabled=False,
                counterpart_count=0,
                relationship_count=0,
                fact_count=0,
                grant_count=0,
                active_grant_count=0,
                summary_count=0,
                visible_fact_count=0,
                denied_fact_count=0,
                config_clamping_disclosures=tuple(self.config.clamping_disclosures),
            )
        return build_stats(self.store, self.sharing, self.config, owner_scope, now=now)


__all__ = [
    "CounterpartWriteResult",
    "ExpiryResult",
    "FactWriteResult",
    "InteractionWriteResult",
    "MergeWriteResult",
    "SocialMemorySystem",
]
