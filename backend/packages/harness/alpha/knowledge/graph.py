"""Cross-Enterprise Knowledge Graph Engine.

Inspired by Chapter 28 of the Master Architecture Blueprint:
- Entity-Relationship associative graph layer (Services, Repositories, Databases, People, Tools, Vendors)
- Multi-hop associative dependency resolution without context window bloat
- Blast-radius impact analysis: "Which systems depend on Vendor E or Database X?"
- Path finding and semantic relationship querying
"""

from __future__ import annotations

import collections
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EntityType(str, Enum):
    SERVICE = "service"
    REPOSITORY = "repository"
    DATABASE = "database"
    TOOL = "tool"
    PERSON = "person"
    VENDOR = "vendor"
    PRODUCT = "product"
    INFRASTRUCTURE = "infrastructure"
    CUSTOM = "custom"


class RelationType(str, Enum):
    DEPENDS_ON = "depends_on"
    OWNS = "owns"
    USES = "uses"
    EMPLOYS = "employs"
    CALLS = "calls"
    CONTRACTS_WITH = "contracts_with"
    APPROVED_BY = "approved_by"
    DEPLOYS_TO = "deploys_to"
    MONITORS = "monitors"
    CUSTOM = "custom"


@dataclass
class EntityNode:
    entity_id: str
    name: str
    entity_type: EntityType = EntityType.CUSTOM
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["entity_type"] = self.entity_type.value
        return data


@dataclass
class RelationEdge:
    source_id: str
    relation_type: RelationType
    target_id: str
    weight: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["relation_type"] = self.relation_type.value
        return data


class KnowledgeGraph:
    """Enterprise-wide Knowledge Graph supporting multi-hop queries and blast-radius analysis."""

    def __init__(self):
        self._entities: dict[str, EntityNode] = {}
        # Outgoing: source_id -> list of RelationEdge
        self._outgoing: dict[str, list[RelationEdge]] = {}
        # Incoming: target_id -> list of RelationEdge
        self._incoming: dict[str, list[RelationEdge]] = {}

    def add_entity(
        self,
        entity_id: str,
        name: str,
        entity_type: EntityType = EntityType.CUSTOM,
        properties: dict[str, Any] | None = None,
    ) -> EntityNode:
        node = EntityNode(
            entity_id=entity_id.strip(),
            name=name.strip(),
            entity_type=entity_type,
            properties=properties or {},
        )
        self._entities[node.entity_id] = node
        self._outgoing.setdefault(node.entity_id, [])
        self._incoming.setdefault(node.entity_id, [])
        return node

    def add_relation(
        self,
        source_id: str,
        relation_type: RelationType,
        target_id: str,
        weight: float = 1.0,
        properties: dict[str, Any] | None = None,
    ) -> RelationEdge:
        src = source_id.strip()
        tgt = target_id.strip()
        if src not in self._entities:
            raise KeyError(f"Source entity '{src}' not found in Knowledge Graph.")
        if tgt not in self._entities:
            raise KeyError(f"Target entity '{tgt}' not found in Knowledge Graph.")

        edge = RelationEdge(
            source_id=src,
            relation_type=relation_type,
            target_id=tgt,
            weight=weight,
            properties=properties or {},
        )
        self._outgoing[src].append(edge)
        self._incoming[tgt].append(edge)
        return edge

    def get_entity(self, entity_id: str) -> EntityNode | None:
        return self._entities.get(entity_id.strip())

    def query_by_relation(
        self,
        source_id: str,
        relation_type: RelationType | None = None,
    ) -> list[dict[str, Any]]:
        src = source_id.strip()
        edges = self._outgoing.get(src, [])
        if relation_type:
            edges = [e for e in edges if e.relation_type == relation_type]

        results = []
        for e in edges:
            tgt_node = self._entities.get(e.target_id)
            if tgt_node:
                results.append({
                    "relation": e.relation_type.value,
                    "target_entity": tgt_node.to_dict(),
                    "properties": e.properties,
                })
        return results

    def query_dependencies(self, entity_id: str, max_depth: int = 4) -> dict[str, Any]:
        """Find all upstream entities that this entity depends on or uses directly or transitively."""
        start = entity_id.strip()
        if start not in self._entities:
            raise KeyError(f"Entity '{start}' not found.")

        visited: set[str] = set()
        queue: collections.deque[tuple[str, int]] = collections.deque([(start, 0)])
        dependencies: list[dict[str, Any]] = []

        while queue:
            curr_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            for edge in self._outgoing.get(curr_id, []):
                # Count dependencies, uses, calls, contracts_with
                if edge.relation_type in (RelationType.DEPENDS_ON, RelationType.USES, RelationType.CALLS, RelationType.CONTRACTS_WITH):
                    tgt_id = edge.target_id
                    if tgt_id not in visited:
                        visited.add(tgt_id)
                        tgt_node = self._entities.get(tgt_id)
                        if tgt_node:
                            dependencies.append({
                                "entity": tgt_node.to_dict(),
                                "relation": edge.relation_type.value,
                                "depth": depth + 1,
                                "via": curr_id,
                            })
                            queue.append((tgt_id, depth + 1))

        return {
            "root_entity": self._entities[start].to_dict(),
            "total_dependencies": len(dependencies),
            "dependencies": dependencies,
        }

    def impact_analysis(self, entity_id: str, max_depth: int = 4) -> dict[str, Any]:
        """Blast-radius analysis: Find all downstream systems/services that depend on this entity."""
        target = entity_id.strip()
        if target not in self._entities:
            raise KeyError(f"Entity '{target}' not found.")

        visited: set[str] = set()
        queue: collections.deque[tuple[str, int]] = collections.deque([(target, 0)])
        affected_entities: list[dict[str, Any]] = []

        while queue:
            curr_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            for edge in self._incoming.get(curr_id, []):
                src_id = edge.source_id
                if src_id not in visited:
                    visited.add(src_id)
                    src_node = self._entities.get(src_id)
                    if src_node:
                        affected_entities.append({
                            "entity": src_node.to_dict(),
                            "relation": edge.relation_type.value,
                            "depth": depth + 1,
                            "impacted_by": curr_id,
                        })
                        queue.append((src_id, depth + 1))

        return {
            "origin_entity": self._entities[target].to_dict(),
            "blast_radius_count": len(affected_entities),
            "affected_systems": affected_entities,
        }

    def find_path(self, source_id: str, target_id: str) -> list[str] | None:
        """Shortest path (BFS) between two entities in the enterprise graph."""
        src = source_id.strip()
        tgt = target_id.strip()
        if src not in self._entities or tgt not in self._entities:
            return None

        visited: set[str] = {src}
        queue: collections.deque[list[str]] = collections.deque([[src]])

        while queue:
            path = queue.popleft()
            node = path[-1]
            if node == tgt:
                return path

            for edge in self._outgoing.get(node, []):
                if edge.target_id not in visited:
                    visited.add(edge.target_id)
                    queue.append(path + [edge.target_id])

        return None

    def to_dict(self) -> dict[str, Any]:
        all_edges = []
        for edge_list in self._outgoing.values():
            all_edges.extend([e.to_dict() for e in edge_list])
        return {
            "entity_count": len(self._entities),
            "relation_count": len(all_edges),
            "entities": [n.to_dict() for n in self._entities.values()],
            "relations": all_edges,
        }
