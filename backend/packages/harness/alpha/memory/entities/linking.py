"""Pure alias indexing, entity linking, and cycle-safe alias-chain flattening.

The alias-resolution pattern is conceptual, inspired by entity linking in Mem0
and Zep/Graphiti.  This module is an original deterministic implementation: it
uses Unicode name normalization, a typed alias index, token Jaccard, substring
evidence, corporate-suffix base matching, and a disclosed type-match factor.
No third-party source code is copied.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .models import Entity, EntityLink, EntityLinkRelation, EntityType, normalized_name

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_COMPACT_RE = re.compile(r"[^\w]+", re.UNICODE)
_CORPORATE_SUFFIXES = frozenset(
    {
        "ag",
        "bv",
        "co",
        "company",
        "corp",
        "corporation",
        "gmbh",
        "inc",
        "incorporated",
        "limited",
        "llc",
        "ltd",
        "plc",
        "sa",
        "sarl",
    }
)


def name_tokens(name: str) -> tuple[str, ...]:
    """Return deterministic Unicode word tokens for an entity name."""
    return tuple(_WORD_RE.findall(normalized_name(name)))


def _name_keys(name: str, *, include_base: bool = True) -> frozenset[str]:
    normalized = normalized_name(name)
    tokens = name_tokens(name)
    keys = {normalized} if normalized else set()
    compact = _COMPACT_RE.sub("", normalized)
    if len(compact) >= 2:
        keys.add(f"compact:{compact}")
    if len(tokens) >= 2:
        acronym = "".join(token[0] for token in tokens if token)
        if len(acronym) >= 2:
            keys.add(f"acronym:{acronym}")
    if include_base:
        if len(tokens) >= 2 and tokens[-1] in _CORPORATE_SUFFIXES:
            base_tokens = tokens[:-1]
        else:
            base_tokens = tokens
        if base_tokens:
            base = " ".join(base_tokens)
            keys.add(f"base:{base}")
            base_compact = _COMPACT_RE.sub("", base)
            if len(base_compact) >= 2:
                keys.add(f"base-compact:{base_compact}")
    return frozenset(keys)


def _entity_names(entity: Entity) -> tuple[str, ...]:
    return (entity.canonical_name, *entity.aliases)


def _dedupe_links(links: Iterable[EntityLink]) -> list[EntityLink]:
    selected: dict[tuple[str, str, EntityLinkRelation], EntityLink] = {}
    for link in links:
        key = (link.source_id, link.target_id, link.relation)
        current = selected.get(key)
        if current is None or link.weight > current.weight:
            selected[key] = link
    return [selected[key] for key in sorted(selected, key=lambda item: (item[0], item[1], item[2].value))]


def _creates_alias_cycle(source_id: str, target_id: str, links: Sequence[EntityLink]) -> bool:
    if source_id == target_id:
        return True
    parents = {link.source_id: link.target_id for link in links if link.relation is EntityLinkRelation.ALIAS_OF}
    current = target_id
    visited: set[str] = set()
    while current in parents and current not in visited:
        if current == source_id:
            return True
        visited.add(current)
        current = parents[current]
    return current == source_id


def alias_root(entity_id: str, links: Iterable[EntityLink]) -> str:
    """Follow ``alias_of`` links to a canonical terminal with a cycle guard."""
    parents = {link.source_id: link.target_id for link in links if link.relation is EntityLinkRelation.ALIAS_OF}
    current = entity_id
    visited: set[str] = set()
    while current in parents and current not in visited:
        visited.add(current)
        current = parents[current]
    return current


def flatten_alias_chains(links: Iterable[EntityLink]) -> list[EntityLink]:
    """Remove alias cycles and point every alias directly at its terminal node.

    Edges with a higher weight win; equal-weight edges retain input order.
    If a malformed document already contains a cycle, only the cycle-closing
    edge is discarded.  The function is pure and does not mutate its input.
    """
    materialized = list(links)
    non_alias = [link for link in materialized if link.relation is not EntityLinkRelation.ALIAS_OF]
    alias_edges = [link for link in materialized if link.relation is EntityLinkRelation.ALIAS_OF]
    strongest: dict[str, EntityLink] = {}
    for _index, link in sorted(
        enumerate(alias_edges),
        key=lambda item: (-item[1].weight, item[0]),
    ):
        strongest.setdefault(link.source_id, link)

    accepted: list[EntityLink] = []
    for link in strongest.values():
        if _creates_alias_cycle(link.source_id, link.target_id, accepted):
            continue
        accepted.append(link)

    flattened: list[EntityLink] = list(non_alias)
    for link in accepted:
        target_id = alias_root(link.source_id, accepted)
        if target_id == link.source_id:
            continue
        flattened.append(
            EntityLink(
                source_id=link.source_id,
                target_id=target_id,
                relation=EntityLinkRelation.ALIAS_OF,
                weight=link.weight,
            )
        )
    return _dedupe_links(flattened)


class AliasIndex:
    """Immutable typed lookup index over entity canonical names and aliases."""

    def __init__(self, entities: Iterable[Entity], links: Iterable[EntityLink] = ()) -> None:
        self._entities = {entity.id: entity for entity in entities}
        self._links = flatten_alias_chains(links)
        buckets: dict[tuple[str, str], set[str]] = defaultdict(set)
        for entity in self._entities.values():
            for name in _entity_names(entity):
                for key in _name_keys(name):
                    buckets[(entity.entity_type.value, key)].add(entity.id)
        self._buckets = {key: frozenset(values) for key, values in buckets.items()}

    def resolve(
        self,
        name: str,
        *,
        entity_type: EntityType | str | None = None,
        allow_generated_base: bool = True,
    ) -> Entity | None:
        """Resolve a display name, whitespace variant, acronym, or known alias."""
        if not str(name).strip() or not self._entities:
            return None
        type_values = (entity_type.value if isinstance(entity_type, EntityType) else str(entity_type),) if entity_type is not None else tuple(entity.entity_type.value for entity in self._entities.values())
        matches: set[str] = set()
        query_keys = _name_keys(name)
        for type_value in type_values:
            for key in query_keys:
                if not allow_generated_base and key.startswith(("base:", "base-compact:")):
                    continue
                matches.update(self._buckets.get((type_value, key), ()))
        if not matches:
            return None
        selected = min(
            matches,
            key=lambda entity_id: (
                -self._entities[entity_id].mention_count,
                -self._entities[entity_id].last_seen,
                self._entities[entity_id].normalized_name,
                entity_id,
            ),
        )
        root_id = alias_root(selected, self._links)
        return self._entities.get(root_id) or self._entities[selected]


@dataclass(frozen=True, slots=True)
class MergeCandidate:
    """One scored merge candidate with its pure similarity evidence."""

    entity_id: str
    score: float
    token_jaccard: float
    substring: float
    type_match: float
    corporate_base_match: float

    @property
    def eligible(self) -> bool:
        return self.score > 0.0


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


def _substring_score(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) < 3:
        return 0.0
    return 1.0 if shorter in longer else 0.0


def _corporate_base(name: str) -> str:
    tokens = name_tokens(name)
    if len(tokens) >= 2 and tokens[-1] in _CORPORATE_SUFFIXES:
        return " ".join(tokens[:-1])
    return normalized_name(name)


def _variant_score(query: str, variant: str, type_score: float) -> MergeCandidate:
    query_key = normalized_name(query)
    variant_key = normalized_name(variant)
    token_score = _jaccard(name_tokens(query), name_tokens(variant))
    substring_score = _substring_score(query_key, variant_key)
    base_score = 1.0 if _corporate_base(query) == _corporate_base(variant) else 0.0
    combined = 0.55 * token_score + 0.30 * substring_score + 0.10 * type_score + 0.05 * base_score
    if base_score and type_score:
        # Legal suffix variants (Acme Inc / Acme Corporation) are a deliberate
        # high-signal alias class, still subject to the configured threshold.
        combined = max(combined, 0.90)
    return MergeCandidate(
        entity_id="",
        score=min(1.0, combined),
        token_jaccard=token_score,
        substring=substring_score,
        type_match=type_score,
        corporate_base_match=base_score,
    )


def merge_similarity(
    name: str,
    entity_type: EntityType | str,
    entity: Entity,
) -> MergeCandidate:
    """Score one entity using its canonical name and aliases, without mutation."""
    kind = entity_type if isinstance(entity_type, EntityType) else EntityType(str(entity_type))
    if kind != entity.entity_type:
        return MergeCandidate(
            entity_id=entity.id,
            score=0.0,
            token_jaccard=0.0,
            substring=0.0,
            type_match=0.0,
            corporate_base_match=0.0,
        )
    variants = [_variant_score(name, variant, 1.0) for variant in _entity_names(entity)]
    best = max(
        variants,
        key=lambda item: (item.score, item.token_jaccard, item.substring, item.type_match),
    )
    return MergeCandidate(
        entity_id=entity.id,
        score=best.score,
        token_jaccard=best.token_jaccard,
        substring=best.substring,
        type_match=best.type_match,
        corporate_base_match=best.corporate_base_match,
    )


def rank_merge_candidates(
    name: str,
    entity_type: EntityType | str,
    entities: Iterable[Entity],
) -> list[MergeCandidate]:
    """Return all positive-similarity candidates in deterministic rank order."""
    kind = entity_type if isinstance(entity_type, EntityType) else EntityType(str(entity_type))
    candidates = [merge_similarity(name, kind, entity) for entity in entities]
    candidates = [candidate for candidate in candidates if candidate.eligible]
    candidates.sort(
        key=lambda candidate: (
            -candidate.score,
            -candidate.token_jaccard,
            -candidate.substring,
            candidate.entity_id,
        )
    )
    return candidates


__all__ = [
    "AliasIndex",
    "MergeCandidate",
    "alias_root",
    "flatten_alias_chains",
    "merge_similarity",
    "name_tokens",
    "rank_merge_candidates",
]
