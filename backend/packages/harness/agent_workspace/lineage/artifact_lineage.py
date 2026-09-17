"""Universal Artifact Lineage & Provenance Graph Engine.

Inspired by Chapters 31, 44, and 45 of the Master Architecture Blueprint:
- Comprehensive artifact registry with SHA256 checksums and mime types
- 4-Tier Trust Model: UNVERIFIED, PROVISIONAL, VERIFIED, HUMAN_APPROVED
- Full DAG tracking of upstream origins and downstream impact
- Answering the question: "Where did this claim, number, or file originate?"
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ConfidenceClass(str, Enum):
    UNVERIFIED = "unverified"
    PROVISIONAL = "provisional"
    VERIFIED = "verified"
    HUMAN_APPROVED = "human_approved"


@dataclass
class ArtifactNode:
    """Represents an immutable artifact generated or consumed during execution."""
    artifact_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    task_id: str = ""
    creator: str = "agent"
    mime_type: str = "text/plain"
    sha256_hash: str = ""
    confidence: ConfidenceClass = ConfidenceClass.UNVERIFIED
    created_at: float = field(default_factory=time.time)
    location: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = self.confidence.value
        return data


@dataclass
class LineageEdge:
    """Directed edge representing derivation of target_artifact from source_artifact."""
    source_id: str
    target_id: str
    derivation_action: str  # e.g., "extracted_features", "compiled_report", "verified_claims"
    agent_id: str = "orchestrator"
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ArtifactLineageGraph:
    """Directed Acyclic Graph (DAG) for full causal artifact lineage and provenance tracking."""

    def __init__(self):
        self._artifacts: dict[str, ArtifactNode] = {}
        # Upstream: target_id -> list of incoming edges (sources that produced target)
        self._incoming: dict[str, list[LineageEdge]] = {}
        # Downstream: source_id -> list of outgoing edges (targets derived from source)
        self._outgoing: dict[str, list[LineageEdge]] = {}

    def register_artifact(
        self,
        name: str,
        content: str | bytes | None = None,
        task_id: str = "",
        creator: str = "agent",
        mime_type: str = "text/plain",
        sha256_hash: str | None = None,
        confidence: ConfidenceClass = ConfidenceClass.UNVERIFIED,
        location: str | None = None,
        artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactNode:
        """Register a new artifact in the lineage graph."""
        art_id = artifact_id or str(uuid.uuid4())

        # Compute SHA-256 hash if content provided and hash omitted
        if sha256_hash is None:
            if content is not None:
                if isinstance(content, str):
                    sha256_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                else:
                    sha256_hash = hashlib.sha256(content).hexdigest()
            else:
                sha256_hash = hashlib.sha256(art_id.encode("utf-8")).hexdigest()

        node = ArtifactNode(
            artifact_id=art_id,
            name=name,
            task_id=task_id,
            creator=creator,
            mime_type=mime_type,
            sha256_hash=sha256_hash,
            confidence=confidence,
            created_at=time.time(),
            location=location,
            metadata=metadata or {},
        )
        self._artifacts[art_id] = node
        if art_id not in self._incoming:
            self._incoming[art_id] = []
        if art_id not in self._outgoing:
            self._outgoing[art_id] = []
        return node

    def record_derivation(
        self,
        source_id: str,
        target_id: str,
        derivation_action: str,
        agent_id: str = "orchestrator",
        metadata: dict[str, Any] | None = None,
    ) -> LineageEdge:
        """Record causal derivation edge: source_id -> target_id."""
        if source_id not in self._artifacts:
            raise KeyError(f"Source artifact ID '{source_id}' is not registered.")
        if target_id not in self._artifacts:
            raise KeyError(f"Target artifact ID '{target_id}' is not registered.")

        # Check for circular derivation
        if self._path_exists(source=target_id, destination=source_id):
            raise ValueError(f"Recording derivation from '{source_id}' to '{target_id}' would create a cycle.")

        edge = LineageEdge(
            source_id=source_id,
            target_id=target_id,
            derivation_action=derivation_action,
            agent_id=agent_id,
            timestamp=time.time(),
            metadata=metadata or {},
        )

        self._incoming.setdefault(target_id, []).append(edge)
        self._outgoing.setdefault(source_id, []).append(edge)
        return edge

    def _path_exists(self, source: str, destination: str) -> bool:
        """Helper DFS to check if there is an existing path from source to destination."""
        visited: set[str] = set()
        stack = [source]
        while stack:
            curr = stack.pop()
            if curr == destination:
                return True
            if curr not in visited:
                visited.add(curr)
                for edge in self._outgoing.get(curr, []):
                    stack.append(edge.target_id)
        return False

    def get_artifact(self, artifact_id: str) -> ArtifactNode | None:
        return self._artifacts.get(artifact_id)

    def set_confidence(self, artifact_id: str, confidence: ConfidenceClass) -> ArtifactNode:
        node = self.get_artifact(artifact_id)
        if not node:
            raise KeyError(f"Artifact '{artifact_id}' not found.")
        node.confidence = confidence
        return node

    def get_upstream_provenance(self, artifact_id: str) -> dict[str, Any]:
        """Recursively trace back all ancestral artifacts and actions that produced artifact_id."""
        target = self.get_artifact(artifact_id)
        if not target:
            raise KeyError(f"Artifact '{artifact_id}' not found.")

        ancestor_edges: list[dict[str, Any]] = []
        visited_nodes: set[str] = set()
        root_sources: list[dict[str, Any]] = []

        def dfs_upstream(curr_id: str):
            visited_nodes.add(curr_id)
            incoming = self._incoming.get(curr_id, [])
            if not incoming and curr_id != artifact_id:
                node = self.get_artifact(curr_id)
                if node:
                    root_sources.append(node.to_dict())
            for edge in incoming:
                ancestor_edges.append(edge.to_dict())
                if edge.source_id not in visited_nodes:
                    dfs_upstream(edge.source_id)

        dfs_upstream(artifact_id)

        return {
            "target_artifact": target.to_dict(),
            "root_sources": root_sources,
            "derivation_steps": ancestor_edges,
            "total_ancestors": len(visited_nodes) - 1,
        }

    def get_downstream_impact(self, artifact_id: str) -> dict[str, Any]:
        """Identify all downstream artifacts derived directly or transitively from artifact_id."""
        source = self.get_artifact(artifact_id)
        if not source:
            raise KeyError(f"Artifact '{artifact_id}' not found.")

        downstream_nodes: list[dict[str, Any]] = []
        downstream_edges: list[dict[str, Any]] = []
        visited: set[str] = set()

        def dfs_downstream(curr_id: str):
            for edge in self._outgoing.get(curr_id, []):
                downstream_edges.append(edge.to_dict())
                if edge.target_id not in visited:
                    visited.add(edge.target_id)
                    target_node = self.get_artifact(edge.target_id)
                    if target_node:
                        downstream_nodes.append(target_node.to_dict())
                    dfs_downstream(edge.target_id)

        dfs_downstream(artifact_id)

        return {
            "source_artifact": source.to_dict(),
            "affected_artifacts": downstream_nodes,
            "derivation_edges": downstream_edges,
            "impact_count": len(downstream_nodes),
        }

    def verify_provenance_integrity(self, artifact_id: str) -> dict[str, Any]:
        """Audit the cryptographic and structural chain of custody for an artifact."""
        target = self.get_artifact(artifact_id)
        if not target:
            return {"valid": False, "error": f"Artifact '{artifact_id}' does not exist"}

        provenance = self.get_upstream_provenance(artifact_id)
        missing_nodes: list[str] = []
        empty_hashes: list[str] = []

        for step in provenance["derivation_steps"]:
            s_id = step["source_id"]
            node = self.get_artifact(s_id)
            if not node:
                missing_nodes.append(s_id)
            elif not node.sha256_hash:
                empty_hashes.append(s_id)

        is_valid = len(missing_nodes) == 0 and len(empty_hashes) == 0
        return {
            "valid": is_valid,
            "artifact_id": artifact_id,
            "confidence": target.confidence.value,
            "root_sources_count": len(provenance["root_sources"]),
            "derivation_steps_count": len(provenance["derivation_steps"]),
            "missing_ancestors": missing_nodes,
            "empty_hashes": empty_hashes,
        }

    def render_lineage_ascii(self, artifact_id: str, indent: int = 0) -> str:
        """Render an ASCII tree representing upstream provenance."""
        target = self.get_artifact(artifact_id)
        if not target:
            return "*(Artifact Not Found)*"

        confidence_badge = f"[{target.confidence.value.upper()}]"
        prefix = "  " * indent + f"📄 {target.name} ({confidence_badge}, sha256:{target.sha256_hash[:8]})"
        lines = [prefix]

        for edge in self._incoming.get(artifact_id, []):
            edge_desc = "  " * (indent + 1) + f"↳ [{edge.derivation_action} via {edge.agent_id}]"
            lines.append(edge_desc)
            lines.append(self.render_lineage_ascii(edge.source_id, indent=indent + 2))

        return "\n".join(lines)
