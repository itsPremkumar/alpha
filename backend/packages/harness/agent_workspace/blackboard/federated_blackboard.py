"""Federated Blackboard Architecture with Partitioned Topic Sharding and Digital Stigmergy.

Scales to 1,000+ agent swarms with domain-partitioned spaces, optimistic concurrency leases,
and decaying pheromone environmental traces.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PheromoneType(str, Enum):
    ACTIVITY = "activity"
    FAULT_HOTSPOT = "fault_hotspot"
    HIGH_PRIORITY = "high_priority"
    COMPLETED = "completed"


@dataclass
class PheromoneTrace:
    resource_id: str
    trace_type: PheromoneType
    initial_intensity: float
    deposited_at: float = field(default_factory=time.time)
    decay_rate: float = 0.05  # lambda decay parameter

    def current_intensity(self, now: Optional[float] = None) -> float:
        t = (now or time.time()) - self.deposited_at
        if t < 0:
            return self.initial_intensity
        if self.decay_rate * t > 50:
            return 0.0
        # Exponential decay: I(t) = I0 * exp(-lambda * t)
        return self.initial_intensity * math.exp(-self.decay_rate * t)


@dataclass
class ResourceLease:
    resource_id: str
    owner_agent_id: str
    expires_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) >= self.expires_at


class FederatedBlackboard:
    """Decoupled partitioned blackboard supporting 1,000+ agents without P2P O(N^2) explosion."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Sharded memory: topic -> key -> value
        self._shards: dict[str, dict[str, Any]] = {
            "global": {},
        }
        self._leases: dict[str, ResourceLease] = {}
        self._traces: dict[str, list[PheromoneTrace]] = {}  # resource_id -> traces
        self._subscribers: dict[str, list[str]] = {}  # topic -> list of agent_ids

    # --- Topic Sharding ---

    def _get_or_create_shard(self, topic: str) -> dict[str, Any]:
        with self._lock:
            if topic not in self._shards:
                self._shards[topic] = {}
            return self._shards[topic]

    def write_entry(self, topic: str, key: str, value: Any, agent_id: str = "system") -> None:
        """Writes entry to a specific topic shard and deposits an activity trace."""
        with self._lock:
            shard = self._get_or_create_shard(topic)
            shard[key] = {
                "value": value,
                "written_by": agent_id,
                "timestamp": time.time(),
            }
            self.deposit_trace(f"{topic}:{key}", PheromoneType.ACTIVITY, intensity=1.0)

    def read_entry(self, topic: str, key: str) -> Optional[Any]:
        with self._lock:
            shard = self._shards.get(topic, {})
            entry = shard.get(key)
            return entry["value"] if entry else None

    def list_topic_keys(self, topic: str) -> list[str]:
        with self._lock:
            return list(self._shards.get(topic, {}).keys())

    # --- Concurrency & Leases ---

    def claim_lease(
        self,
        resource_id: str,
        agent_id: str,
        duration_seconds: float = 30.0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Atomically acquires an optimistic concurrency lease on a resource."""
        with self._lock:
            now = time.time()
            existing = self._leases.get(resource_id)
            if existing and not existing.is_expired(now):
                if existing.owner_agent_id != agent_id:
                    return False  # Held by another active agent

            self._leases[resource_id] = ResourceLease(
                resource_id=resource_id,
                owner_agent_id=agent_id,
                expires_at=now + duration_seconds,
                metadata=metadata or {},
            )
            return True

    def release_lease(self, resource_id: str, agent_id: str) -> bool:
        with self._lock:
            existing = self._leases.get(resource_id)
            if not existing:
                return True
            if existing.owner_agent_id == agent_id or existing.is_expired():
                self._leases.pop(resource_id, None)
                return True
            return False

    def is_locked(self, resource_id: str) -> bool:
        with self._lock:
            existing = self._leases.get(resource_id)
            return existing is not None and not existing.is_expired()

    # --- Digital Stigmergy (Pheromones & Environmental Traces) ---

    def deposit_trace(
        self,
        resource_id: str,
        trace_type: PheromoneType,
        intensity: float = 1.0,
        decay_rate: float = 0.05,
    ) -> None:
        """Deposits or reinforces an environmental pheromone trace."""
        with self._lock:
            trace = PheromoneTrace(
                resource_id=resource_id,
                trace_type=trace_type,
                initial_intensity=intensity,
                decay_rate=decay_rate,
            )
            self._traces.setdefault(resource_id, []).append(trace)

    def get_intensity(self, resource_id: str, trace_type: Optional[PheromoneType] = None) -> float:
        """Calculates total current intensity of pheromone traces on a resource."""
        with self._lock:
            now = time.time()
            traces = self._traces.get(resource_id, [])
            total = 0.0
            active_traces = []

            for t in traces:
                curr = t.current_intensity(now)
                if curr > 0.01:  # Retain non-evaporated traces
                    active_traces.append(t)
                    if trace_type is None or t.trace_type == trace_type:
                        total += curr

            self._traces[resource_id] = active_traces
            return total

    def get_stigmergic_heatmap(self, trace_type: Optional[PheromoneType] = None) -> dict[str, float]:
        """Returns normalized heatmap of all environmental traces for swarm self-organization."""
        with self._lock:
            heatmap: dict[str, float] = {}
            for res_id in list(self._traces.keys()):
                intensity = self.get_intensity(res_id, trace_type=trace_type)
                if intensity > 0.01:
                    heatmap[res_id] = round(intensity, 3)
            return heatmap
