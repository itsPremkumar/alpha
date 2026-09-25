"""Entity-scoped recall over the raw-memory entity index."""

from __future__ import annotations

import html
from collections import Counter, deque
from collections.abc import Mapping

from .config import EntityConfig
from .linking import AliasIndex
from .models import Entity
from .store import EntityStore

MAX_NEIGHBORHOOD_DEPTH = 3
_MAX_RENDER_LINES = 20
_MAX_RENDER_CHARS = 8_000


class EntityRecall:
    """Read-only, config-gated recall surface over an injected entity store."""

    def __init__(self, config: EntityConfig, store: EntityStore) -> None:
        self.config = config
        self.store = store

    def resolve(
        self,
        name: str,
        *,
        user_id: str,
        include_unpromoted: bool = False,
    ) -> Entity | None:
        """Resolve a canonical name, whitespace variant, acronym, or stored alias."""
        if not self.config.enabled or not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(user_id, str) or not user_id.strip():
            return None
        entities, _mentions, links, _evictions = self.store.read_snapshot(user_id)
        entity = AliasIndex(entities, links).resolve(name)
        if entity is None:
            return None
        if not include_unpromoted and not entity.is_promoted(self.config.min_mentions_to_promote):
            return None
        return entity.model_copy(deep=True)

    def entities_for_record(
        self,
        record_id: str,
        *,
        user_id: str,
        include_unpromoted: bool = False,
    ) -> list[Entity]:
        """Return retained entities explicitly mentioned by one source record."""
        if not self.config.enabled or not isinstance(record_id, str) or not record_id.strip():
            return []
        if not isinstance(user_id, str) or not user_id.strip():
            return []
        entities, mentions, _links, _evictions = self.store.read_snapshot(user_id)
        by_id = {entity.id: entity for entity in entities}
        ids = {mention.entity_id for mention in mentions if mention.record_id == record_id}
        matched = [by_id[entity_id] for entity_id in ids if entity_id in by_id]
        entities = matched
        if not include_unpromoted:
            entities = [entity for entity in entities if entity.is_promoted(self.config.min_mentions_to_promote)]
        return sorted(
            (entity.model_copy(deep=True) for entity in entities),
            key=lambda item: (item.normalized_name, item.id),
        )

    def records_for_entity(
        self,
        name: str,
        *,
        user_id: str,
        include_unpromoted: bool = False,
    ) -> list[str]:
        """Return source record ids for a resolved entity, never fabricated content."""
        entity = self.resolve(
            name,
            user_id=user_id,
            include_unpromoted=include_unpromoted,
        )
        if entity is None:
            return []
        return sorted({mention.record_id for mention in self.store.list_mentions(user_id, entity_id=entity.id)})

    def neighborhood(
        self,
        entity: str | Entity,
        *,
        user_id: str,
        depth: int = 1,
        include_unpromoted: bool = False,
    ) -> list[Entity]:
        """Traverse related and alias links with visited-cycle protection and a hard depth cap."""
        if not self.config.enabled or not isinstance(user_id, str) or not user_id.strip():
            return []
        root = (
            entity
            if isinstance(entity, Entity)
            else self.resolve(
                entity,
                user_id=user_id,
                include_unpromoted=include_unpromoted,
            )
        )
        if root is None:
            return []
        max_depth = min(max(0, int(depth)), MAX_NEIGHBORHOOD_DEPTH)
        if max_depth == 0:
            return []
        entities, _mentions, links, _evictions = self.store.read_snapshot(user_id)
        by_id = {entity.id: entity for entity in entities}
        if root.id not in by_id:
            return []
        adjacency: dict[str, set[str]] = {entity_id: set() for entity_id in by_id}
        for link in links:
            if link.source_id in adjacency and link.target_id in adjacency:
                adjacency[link.source_id].add(link.target_id)
                adjacency[link.target_id].add(link.source_id)
        visited = {root.id}
        queue = deque([(root.id, 0)])
        found: list[Entity] = []
        while queue:
            current_id, current_depth = queue.popleft()
            if current_depth >= max_depth:
                continue
            for neighbor_id in sorted(adjacency.get(current_id, ())):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                neighbor = by_id[neighbor_id]
                if include_unpromoted or neighbor.is_promoted(self.config.min_mentions_to_promote):
                    found.append(neighbor.model_copy(deep=True))
                queue.append((neighbor_id, current_depth + 1))
        return sorted(found, key=lambda item: (item.normalized_name, item.id))

    def render_block(
        self,
        name: str | None = None,
        *,
        user_id: str,
        max_lines: int = 8,
        max_chars: int = 2_400,
        include_unpromoted: bool = False,
    ) -> str:
        """Render a bounded, escaped entity block or an honest empty string.

        With a name, one resolved entity is rendered.  Without a name, the most
        mentioned retained entities are rendered in deterministic order; this
        is the form used by a prompt seam that has no separate query argument.
        """
        if not self.config.enabled:
            return ""
        if not isinstance(user_id, str) or not user_id.strip():
            return ""
        status = self.store.read_status(user_id)
        if status != "ok":
            return f"### Entity memory\n- Unavailable: {html.escape(status)}; no entity facts were inferred."

        entities, mentions, links, _evictions = self.store.read_snapshot(user_id)
        if isinstance(name, str) and name.strip():
            resolved = AliasIndex(entities, links).resolve(name)
            targets = [resolved] if resolved is not None and (include_unpromoted or resolved.is_promoted(self.config.min_mentions_to_promote)) else []
        else:
            targets = [entity for entity in entities if include_unpromoted or entity.is_promoted(self.config.min_mentions_to_promote)]
            targets.sort(
                key=lambda entity: (
                    -entity.mention_count,
                    -entity.last_seen,
                    entity.normalized_name,
                    entity.id,
                )
            )
        if not targets:
            return ""

        header = "### Entity memory"
        line_limit = min(max(1, int(max_lines)), _MAX_RENDER_LINES)
        requested_chars = int(max_chars)
        if requested_chars < len(header) + 2:
            return ""
        char_limit = min(requested_chars, _MAX_RENDER_CHARS)
        lines = [header]
        remaining = char_limit - len(header) - 1
        records_by_entity: dict[str, list[str]] = {}
        for mention in mentions:
            records_by_entity.setdefault(mention.entity_id, []).append(mention.record_id)
        for entity in targets[:line_limit]:
            aliases = ", ".join(entity.aliases[:4]) or "none"
            records = ", ".join(sorted(set(records_by_entity.get(entity.id, ())))) or "none"
            raw_line = f"- [{entity.entity_type.value}] {entity.canonical_name}; aliases: {aliases}; mentions: {entity.mention_count}; records: {records}"
            escaped = _escaped_prefix(raw_line, remaining)
            if not escaped:
                break
            lines.append(escaped)
            remaining -= len(escaped) + 1
            if remaining < 0 or len(lines) >= line_limit + 1:
                break
        return "\n".join(lines)[:char_limit]

    def stats(self, user_id: str) -> dict[str, int | str | dict[str, int]]:
        """Return deterministic, store-backed counts without claiming missing data."""
        if not self.config.enabled:
            return {
                "status": "disabled",
                "entities": 0,
                "promoted_entities": 0,
                "pending_entities": 0,
                "mentions": 0,
                "links": 0,
                "evictions": 0,
                "types": {},
            }
        if not isinstance(user_id, str) or not user_id.strip():
            return {
                "status": "missing_user_scope",
                "entities": 0,
                "promoted_entities": 0,
                "pending_entities": 0,
                "mentions": 0,
                "links": 0,
                "evictions": 0,
                "types": {},
            }
        status = self.store.read_status(user_id)
        entities, mentions, links, evictions = self.store.read_snapshot(user_id)
        mention_count = len(mentions)
        link_count = len(links)
        eviction_count = len(evictions)
        promoted = sum(entity.is_promoted(self.config.min_mentions_to_promote) for entity in entities)
        by_type = Counter(entity.entity_type.value for entity in entities)
        return {
            "status": status,
            "entities": len(entities),
            "promoted_entities": promoted,
            "pending_entities": len(entities) - promoted,
            "mentions": mention_count,
            "links": link_count,
            "evictions": eviction_count,
            "types": dict(sorted(by_type.items())),
        }


def _escaped_prefix(value: str, limit: int) -> str:
    output: list[str] = []
    used = 0
    for character in value:
        escaped = html.escape(character, quote=True)
        if used + len(escaped) > max(0, limit):
            break
        output.append(escaped)
        used += len(escaped)
    return "".join(output)


def resolve(recall: EntityRecall, name: str, **kwargs: object) -> Entity | None:
    return recall.resolve(name, **kwargs)  # type: ignore[arg-type]


def entities_for_record(recall: EntityRecall, record_id: str, **kwargs: object) -> list[Entity]:
    return recall.entities_for_record(record_id, **kwargs)  # type: ignore[arg-type]


def records_for_entity(recall: EntityRecall, name: str, **kwargs: object) -> list[str]:
    return recall.records_for_entity(name, **kwargs)  # type: ignore[arg-type]


def neighborhood(
    recall: EntityRecall,
    entity: str | Entity,
    **kwargs: object,
) -> list[Entity]:
    return recall.neighborhood(entity, **kwargs)  # type: ignore[arg-type]


def render_block(recall: EntityRecall, name: str | None = None, **kwargs: object) -> str:
    return recall.render_block(name, **kwargs)  # type: ignore[arg-type]


def stats(recall: EntityRecall, user_id: str) -> Mapping[str, int | str | dict[str, int]]:
    return recall.stats(user_id)


__all__ = [
    "MAX_NEIGHBORHOOD_DEPTH",
    "EntityRecall",
    "entities_for_record",
    "neighborhood",
    "records_for_entity",
    "render_block",
    "resolve",
    "stats",
]
