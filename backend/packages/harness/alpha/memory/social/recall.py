"""Bounded, honest recall blocks for relationships and audience-shared facts."""

from __future__ import annotations

import html
import time
from dataclasses import dataclass, field
from typing import Literal

from .config import SocialConfig
from .relationships import RelationshipManager
from .sharing import SharingManager

_MAX_RECALL_ITEMS = 20


@dataclass(frozen=True, slots=True)
class RecallBlock:
    text: str
    status: Literal["ok", "empty", "disabled"]
    reason: str
    limit: int
    entry_count: int = 0
    grant_disclosures: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SocialStats:
    owner_scope: str
    enabled: bool
    counterpart_count: int
    relationship_count: int
    fact_count: int
    grant_count: int
    active_grant_count: int
    summary_count: int
    visible_fact_count: int
    denied_fact_count: int
    config_clamping_disclosures: tuple[str, ...]


def _bounded_limit(limit: int) -> int:
    return min(_MAX_RECALL_ITEMS, max(0, int(limit)))


def relationship_block(
    manager: RelationshipManager,
    config: SocialConfig,
    owner_scope: str,
    *,
    limit: int = 5,
    now: float | None = None,
) -> RecallBlock:
    """Render at most ``limit`` counterpart/trust/topic lines for one scope."""

    bounded_limit = _bounded_limit(limit)
    if not config.enabled:
        return RecallBlock(
            text="Social relationship memory is disabled by configuration.",
            status="disabled",
            reason="social_memory_disabled",
            limit=bounded_limit,
        )
    ranked = manager.top_counterparts(owner_scope, limit=bounded_limit, now=now) if bounded_limit else []
    if not ranked:
        return RecallBlock(
            text=f"No social relationship memory is recorded for scope '{html.escape(owner_scope, quote=True)}'.",
            status="empty",
            reason="no_relationships",
            limit=bounded_limit,
        )
    at = time.time() if now is None else float(now)
    lines = ["### Social relationships"]
    for item in ranked:
        topics = ", ".join(item.relationship.shared_topics[-5:]) or "none recorded"
        display_name = html.escape(item.counterpart.display_name, quote=False)
        escaped_topics = html.escape(topics, quote=False)
        lines.append(f"- {display_name} [{html.escape(item.counterpart.kind, quote=False)}; trust {item.decayed_trust:.3f}; shared topics: {escaped_topics}]")
    return RecallBlock(
        text="\n".join(lines),
        status="ok",
        reason=f"{len(ranked)} relationship(s) selected at {at:.0f}",
        limit=bounded_limit,
        entry_count=len(ranked),
    )


def shared_context(
    sharing: SharingManager,
    config: SocialConfig,
    reader_scope: str,
    *,
    limit: int = 5,
    now: float | None = None,
) -> RecallBlock:
    """Render authorized facts; every shared line names the grant that allowed it."""

    bounded_limit = _bounded_limit(limit)
    if not config.enabled:
        return RecallBlock(
            text="Audience-scoped shared memory is disabled by configuration.",
            status="disabled",
            reason="social_memory_disabled",
            limit=bounded_limit,
        )
    visible = sharing.visible_facts(reader_scope, now=now)
    selected = visible.facts[:bounded_limit]
    if not selected:
        denied_reasons = ", ".join(sorted({decision.reason for decision in visible.denied})) or visible.reason
        return RecallBlock(
            text=(f"No audience-authorized shared facts are visible to scope '{html.escape(reader_scope, quote=True)}' ({html.escape(denied_reasons, quote=False)})."),
            status="empty",
            reason=denied_reasons,
            limit=bounded_limit,
            entry_count=0,
        )

    allowed_by_id = {decision.fact_id: decision for decision in visible.allowed}
    lines = ["### Audience-scoped shared memory"]
    disclosures: list[str] = []
    for fact in selected:
        decision = allowed_by_id[fact.id]
        content = html.escape(fact.content, quote=False)
        if decision.cross_scope and decision.grant is not None:
            grant = decision.grant
            disclosure = f"grant:{grant.id}; owner:{grant.owner_scope}; reader:{grant.grantee_scope}; expires:{grant.expires_at or 'never'}"
            disclosures.append(disclosure)
            lines.append(f"- {content} [audience disclosure: {html.escape(disclosure, quote=False)}]")
        else:
            lines.append(f"- {content} [owner scope]")
    return RecallBlock(
        text="\n".join(lines),
        status="ok",
        reason=visible.reason,
        limit=bounded_limit,
        entry_count=len(selected),
        grant_disclosures=tuple(disclosures),
    )


def stats(
    store,
    sharing: SharingManager,
    config: SocialConfig,
    owner_scope: str,
    *,
    now: float | None = None,
) -> SocialStats:
    """Return counts only; stats never expose fact bodies or cross-scope content."""

    at = time.time() if now is None else float(now)
    snapshot = store.snapshot(owner_scope)
    visible = sharing.visible_facts(reader_scope=owner_scope, now=at)
    active_grants = sum(grant.is_active(at) for grant in snapshot.grants)
    return SocialStats(
        owner_scope=owner_scope,
        enabled=config.enabled,
        counterpart_count=len(snapshot.counterparts),
        relationship_count=len(snapshot.relationships),
        fact_count=len(snapshot.facts),
        grant_count=len(snapshot.grants),
        active_grant_count=active_grants,
        summary_count=len(snapshot.summaries),
        visible_fact_count=len(visible.facts),
        denied_fact_count=len(visible.denied),
        config_clamping_disclosures=tuple(config.clamping_disclosures),
    )


__all__ = ["RecallBlock", "SocialStats", "relationship_block", "shared_context", "stats"]
