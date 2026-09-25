"""Counterpart identity resolution and time-decayed relationship state."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .config import SocialConfig
from .models import Counterpart, InteractionSummary, Relationship, normalize_scope
from .store import ScopeState, SocialStore

_OUTCOME_TRUST_DELTA: dict[str, float] = {
    "completed": 0.05,
    "partial": 0.01,
    "failed": -0.08,
    "cancelled": -0.02,
    "unknown": 0.0,
}


def decayed_trust(trust: float, *, elapsed_days: float, half_life_days: float) -> float:
    """Exponential half-life decay, clamped to the valid trust interval."""

    elapsed = max(0.0, float(elapsed_days))
    half_life = max(0.01, float(half_life_days))
    return min(1.0, max(0.0, float(trust) * math.exp(-math.log(2.0) * elapsed / half_life)))


@dataclass(frozen=True, slots=True)
class RankedCounterpart:
    counterpart: Counterpart
    relationship: Relationship
    decayed_trust: float


@dataclass(frozen=True, slots=True)
class RelationshipUpdate:
    counterpart: Counterpart
    relationship: Relationship
    summary: InteractionSummary


class RelationshipManager:
    """Owner-scoped identity and relationship operations."""

    def __init__(self, store: SocialStore, config: SocialConfig) -> None:
        self.store = store
        self.config = config

    @staticmethod
    def _resolve_in_document(doc: ScopeState, candidate: Counterpart) -> Counterpart | None:
        candidate_keys = candidate.identity_keys()
        for existing in doc.counterparts.values():
            if existing.id == candidate.id or existing.identity_keys() & candidate_keys:
                return existing
        return None

    def resolve_counterpart_id(self, owner_scope: str, identity: str) -> str | None:
        """Resolve an id, display name, or alias with Unicode-aware case folding."""

        scope = normalize_scope(owner_scope)
        key = str(identity or "").strip().casefold()
        if not key:
            return None
        for counterpart in self.store.list_counterparts(scope):
            if key in counterpart.identity_keys():
                return counterpart.id
        return None

    def _upsert_in_document(
        self,
        doc: ScopeState,
        counterpart: Counterpart,
        now: float,
    ) -> Counterpart:
        existing = self._resolve_in_document(doc, counterpart)
        if existing is None:
            doc.counterparts[counterpart.id] = counterpart.model_copy(deep=True)
            return counterpart.model_copy(deep=True)

        aliases = set(existing.aliases)
        aliases.update(counterpart.aliases)
        if counterpart.id != existing.id:
            aliases.add(counterpart.id)
        merged = existing.model_copy(
            deep=True,
            update={
                "display_name": counterpart.display_name or existing.display_name,
                "aliases": sorted(aliases, key=str.casefold),
                "first_seen": min(existing.first_seen, counterpart.first_seen, now),
                "last_seen": max(existing.last_seen, counterpart.last_seen, now),
                "interaction_count": max(existing.interaction_count, counterpart.interaction_count),
                "traits": {**existing.traits, **counterpart.traits},
                "metadata": {**existing.metadata, **counterpart.metadata},
            },
        )
        doc.counterparts[existing.id] = merged
        doc.counterparts.pop(counterpart.id, None) if counterpart.id != existing.id else None
        return merged.model_copy(deep=True)

    def upsert_counterpart(
        self,
        owner_scope: str,
        counterpart: Counterpart,
        *,
        now: float | None = None,
    ) -> Counterpart:
        at = time.time() if now is None else float(now)

        def update(doc: ScopeState) -> Counterpart:
            return self._upsert_in_document(doc, counterpart, at)

        return self.store.update_scope(owner_scope, update, now=at)

    def record_interaction(
        self,
        owner_scope: str,
        counterpart: Counterpart,
        summary: InteractionSummary,
        *,
        topics: list[str] | tuple[str, ...] = (),
        now: float | None = None,
    ) -> RelationshipUpdate:
        """Atomically update counterpart count, trust, topics, and summary."""

        at = time.time() if now is None else float(now)
        supplied_topics = [str(topic).strip() for topic in topics if str(topic).strip()]

        def update(doc: ScopeState) -> RelationshipUpdate:
            peer = self._upsert_in_document(doc, counterpart, at)
            interaction_count = max(1, summary.interaction_count)
            peer.interaction_count += interaction_count
            peer.first_seen = min(peer.first_seen or at, at)
            peer.last_seen = max(peer.last_seen or at, at)
            doc.counterparts[peer.id] = peer

            clean_topics = list(dict.fromkeys([*summary.topics, *supplied_topics]))
            bounded_summary = summary.model_copy(
                deep=True,
                update={"counterpart_id": peer.id, "topics": clean_topics, "created_at": at},
            )
            relationship = doc.relationships.get(peer.id)
            if relationship is None:
                trust = 0.2 + _OUTCOME_TRUST_DELTA.get(summary.status, 0.0)
                relationship = Relationship(
                    owner_scope=doc.owner_scope,
                    counterpart_id=peer.id,
                    trust=trust,
                    shared_topic_count=len(clean_topics),
                    shared_topics=clean_topics,
                    last_interaction_at=at,
                    decay_half_life_days=self.config.relationship_decay_half_life_days,
                )
            else:
                elapsed_days = max(0.0, at - relationship.last_interaction_at) / 86_400.0
                trust = decayed_trust(
                    relationship.trust,
                    elapsed_days=elapsed_days,
                    half_life_days=relationship.decay_half_life_days,
                )
                topics_union = list(dict.fromkeys([*relationship.shared_topics, *clean_topics]))
                relationship = relationship.model_copy(
                    deep=True,
                    update={
                        "trust": trust + _OUTCOME_TRUST_DELTA.get(summary.status, 0.0),
                        "shared_topic_count": len(topics_union),
                        "shared_topics": topics_union,
                        "last_interaction_at": at,
                        "decay_half_life_days": self.config.relationship_decay_half_life_days,
                    },
                )
            relationship = Relationship.model_validate(relationship.model_dump())
            doc.relationships[peer.id] = relationship
            doc.summaries.append(bounded_summary)
            return RelationshipUpdate(
                counterpart=peer.model_copy(deep=True),
                relationship=relationship.model_copy(deep=True),
                summary=bounded_summary.model_copy(deep=True),
            )

        return self.store.update_scope(owner_scope, update, now=at)

    def merge_counterparts(
        self,
        owner_scope: str,
        source_id: str,
        target_id: str,
        *,
        reason: str,
        now: float | None = None,
    ) -> Counterpart:
        """Merge two known identities in one scope without double-counting activity."""

        if source_id == target_id:
            raise ValueError("source_id and target_id must differ")
        merge_reason = str(reason or "").strip()
        if not merge_reason:
            raise ValueError("counterpart merge requires a reason")
        at = time.time() if now is None else float(now)

        def update(doc: ScopeState) -> Counterpart:
            source = doc.counterparts.get(source_id)
            target = doc.counterparts.get(target_id)
            if source is None or target is None:
                raise KeyError("both counterpart ids must exist in the owner scope")

            aliases = set(target.aliases)
            aliases.update(source.aliases)
            aliases.update({source.id, source.display_name})
            merged = target.model_copy(
                deep=True,
                update={
                    "aliases": sorted(aliases, key=str.casefold),
                    "first_seen": min(source.first_seen, target.first_seen),
                    "last_seen": max(source.last_seen, target.last_seen),
                    "interaction_count": max(source.interaction_count, target.interaction_count),
                    "traits": {**source.traits, **target.traits},
                    "metadata": {**source.metadata, **target.metadata},
                },
            )
            doc.counterparts[target_id] = merged
            doc.counterparts.pop(source_id, None)

            source_relationship = doc.relationships.pop(source_id, None)
            target_relationship = doc.relationships.get(target_id)
            if source_relationship is not None and target_relationship is None:
                doc.relationships[target_id] = source_relationship.model_copy(deep=True, update={"counterpart_id": target_id})
            elif source_relationship is not None and target_relationship is not None:
                topics = list(dict.fromkeys([*target_relationship.shared_topics, *source_relationship.shared_topics]))
                notes = [note for note in (target_relationship.notes, source_relationship.notes) if note]
                doc.relationships[target_id] = target_relationship.model_copy(
                    deep=True,
                    update={
                        "trust": max(target_relationship.trust, source_relationship.trust),
                        "formality": max(target_relationship.formality, source_relationship.formality),
                        "shared_topic_count": len(topics),
                        "shared_topics": topics,
                        "last_interaction_at": max(
                            target_relationship.last_interaction_at,
                            source_relationship.last_interaction_at,
                        ),
                        "decay_half_life_days": max(
                            target_relationship.decay_half_life_days,
                            source_relationship.decay_half_life_days,
                        ),
                        "notes": " | ".join(dict.fromkeys(notes)),
                    },
                )
            for summary in doc.summaries:
                if summary.counterpart_id == source_id:
                    summary.counterpart_id = target_id
            return merged.model_copy(deep=True)

        return self.store.update_scope(owner_scope, update, now=at)

    def top_counterparts(
        self,
        owner_scope: str,
        *,
        limit: int = 5,
        now: float | None = None,
    ) -> list[RankedCounterpart]:
        at = time.time() if now is None else float(now)
        relationships = {relationship.counterpart_id: relationship for relationship in self.store.list_relationships(owner_scope)}
        ranked: list[RankedCounterpart] = []
        for counterpart in self.store.list_counterparts(owner_scope):
            relationship = relationships.get(counterpart.id)
            if relationship is None:
                continue
            elapsed_days = max(0.0, at - relationship.last_interaction_at) / 86_400.0
            strength = decayed_trust(
                relationship.trust,
                elapsed_days=elapsed_days,
                half_life_days=relationship.decay_half_life_days,
            )
            ranked.append(
                RankedCounterpart(
                    counterpart=counterpart.model_copy(deep=True),
                    relationship=relationship.model_copy(deep=True),
                    decayed_trust=strength,
                )
            )
        ranked.sort(
            key=lambda item: (
                -item.decayed_trust,
                -item.counterpart.interaction_count,
                item.counterpart.id,
            )
        )
        return ranked[: max(1, int(limit))]


__all__ = [
    "RankedCounterpart",
    "RelationshipManager",
    "RelationshipUpdate",
    "decayed_trust",
]
