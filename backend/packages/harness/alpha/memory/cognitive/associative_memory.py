"""Cross-Session Associative Memory Network.

Implements Hebbian association ("cells that fire together wire together")
and spreading activation across distinct cognitive memory tiers.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from alpha.memory.cognitive.models import AssociativeLink, CognitiveTier


def _normalize_tier(val: CognitiveTier | str) -> CognitiveTier:
    if isinstance(val, CognitiveTier):
        return val
    s = str(val).lower().strip()
    if s in ("working", "working_memory", "scratch"):
        return CognitiveTier.WORKING
    if s in ("episodic_flat", "flat", "episodic_trace", "trace"):
        return CognitiveTier.EPISODIC_FLAT
    if s in ("episodic_hierarchical", "hierarchical", "episode"):
        return CognitiveTier.EPISODIC_HIERARCHICAL
    if s in ("semantic_fact", "semantic", "fact", "belief"):
        return CognitiveTier.SEMANTIC_FACT
    if s in ("procedural_skill", "procedural", "skill"):
        return CognitiveTier.PROCEDURAL_SKILL
    if s in ("spatio_temporal", "temporal", "event"):
        return CognitiveTier.SPATIO_TEMPORAL
    if s in ("associative", "network", "link"):
        return CognitiveTier.ASSOCIATIVE
    return CognitiveTier(s)


class AssociativeNetwork:
    """Inter-tier associative network linking related items with spreading activation dynamics."""

    def __init__(self, max_links: int = 5000) -> None:
        self.max_links = max_links
        self._links: dict[str, AssociativeLink] = {}
        # Adjacency index: (tier, item_id) -> list of link_ids
        self._adj: dict[tuple[str, str], list[str]] = defaultdict(list)

    def link_memories(
        self,
        source_tier: CognitiveTier | str,
        source_id: str,
        target_tier: CognitiveTier | str,
        target_id: str,
        initial_weight: float = 0.5,
        link_id: str | None = None,
        co_occurrences: int = 1,
        last_reinforced_at: float | None = None,
    ) -> AssociativeLink:
        """Create or reinforce an associative link between two memory units."""
        s_tier = _normalize_tier(source_tier)
        t_tier = _normalize_tier(target_tier)
        now = time.time()

        # Check existing link
        for link in self._links.values():
            match_forward = (
                link.source_tier == s_tier
                and link.source_id == source_id
                and link.target_tier == t_tier
                and link.target_id == target_id
            )
            match_reverse = (
                link.source_tier == t_tier
                and link.source_id == target_id
                and link.target_tier == s_tier
                and link.target_id == source_id
            )
            if match_forward or match_reverse:
                # Hebbian reinforcement
                link.co_occurrences += max(1, co_occurrences)
                link.weight = min(1.0, link.weight + 0.1)
                link.last_reinforced_at = last_reinforced_at or now
                return link

        link = AssociativeLink(
            source_tier=s_tier,
            source_id=source_id,
            target_tier=t_tier,
            target_id=target_id,
            weight=max(0.0, min(1.0, initial_weight)),
            co_occurrences=co_occurrences,
            last_reinforced_at=last_reinforced_at or now,
        )
        if link_id:
            link.link_id = link_id

        self._links[link.link_id] = link
        self._adj[(s_tier.value, source_id)].append(link.link_id)
        self._adj[(t_tier.value, target_id)].append(link.link_id)
        self._enforce_capacity()
        return link

    def delete_link(self, link_id: str) -> bool:
        """Remove link and clean up bidirectional adjacency indices."""
        link = self._links.pop(link_id, None)
        if not link:
            return False

        # Clean source adj
        src_key = (link.source_tier.value, link.source_id)
        if src_key in self._adj:
            self._adj[src_key] = [lid for lid in self._adj[src_key] if lid != link_id]
            if not self._adj[src_key]:
                del self._adj[src_key]

        # Clean target adj
        tgt_key = (link.target_tier.value, link.target_id)
        if tgt_key in self._adj:
            self._adj[tgt_key] = [lid for lid in self._adj[tgt_key] if lid != link_id]
            if not self._adj[tgt_key]:
                del self._adj[tgt_key]

        return True

    def spread_activation(
        self,
        activated_nodes: list[tuple[CognitiveTier | str, str, float]],
        decay_per_step: float = 0.5,
        max_hops: int = 2,
        activation_threshold: float = 0.10,
    ) -> dict[tuple[str, str], float]:
        """Propagate activation energy from initial nodes across the network."""
        current_energy: dict[tuple[str, str], float] = defaultdict(float)
        for tier, item_id, energy in activated_nodes:
            t_val = tier.value if isinstance(tier, CognitiveTier) else tier
            current_energy[(t_val, item_id)] = max(current_energy[(t_val, item_id)], energy)

        visited: set[tuple[str, str]] = set()

        for hop in range(max_hops):
            next_energy: dict[tuple[str, str], float] = defaultdict(float)
            for (curr_tier, curr_id), energy in list(current_energy.items()):
                if (curr_tier, curr_id) in visited or energy < activation_threshold:
                    continue
                visited.add((curr_tier, curr_id))

                link_ids = self._adj.get((curr_tier, curr_id), [])
                for lid in link_ids:
                    link = self._links.get(lid)
                    if not link:
                        continue

                    # Determine other endpoint
                    if link.source_tier.value == curr_tier and link.source_id == curr_id:
                        other = (link.target_tier.value, link.target_id)
                    else:
                        other = (link.source_tier.value, link.source_id)

                    transferred = energy * link.weight * decay_per_step
                    if transferred >= activation_threshold:
                        next_energy[other] = max(next_energy[other], transferred)

            for k, v in next_energy.items():
                current_energy[k] = max(current_energy[k], v)

        return dict(current_energy)

    def get_associations_for(self, tier: CognitiveTier | str, item_id: str) -> list[AssociativeLink]:
        t_val = tier.value if isinstance(tier, CognitiveTier) else tier
        link_ids = self._adj.get((t_val, item_id), [])
        return [self._links[lid] for lid in link_ids if lid in self._links]

    def _enforce_capacity(self) -> None:
        if len(self._links) <= self.max_links:
            return
        sorted_keys = sorted(
            self._links.keys(),
            key=lambda k: (self._links[k].weight, self._links[k].co_occurrences, self._links[k].last_reinforced_at),
        )
        excess = len(self._links) - self.max_links
        for k in sorted_keys[:excess]:
            self.delete_link(k)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_links": len(self._links),
            "links": [l.to_dict() for l in list(self._links.values())[:100]],
        }
