"""Episodic Memory Engine: Flat Trajectory Traces & Hierarchical Abstracted Episodes.

Captures sequential agent interaction trajectories, execution outcomes,
and abstracts them into multi-level episodic summaries.
"""

from __future__ import annotations

import time
from typing import Any

from alpha.memory.cognitive.models import EpisodicTrace, HierarchicalEpisode, TraceOutcome


class EpisodicMemoryEngine:
    """Stores sequential execution traces (Flat) and synthesized episodes (Hierarchical)."""

    def __init__(self, max_flat_traces: int = 1000, max_episodes: int = 200) -> None:
        self.max_flat_traces = max_flat_traces
        self.max_episodes = max_episodes
        self._traces: dict[str, EpisodicTrace] = {}
        self._episodes: dict[str, HierarchicalEpisode] = {}

    def record_trace(
        self,
        action: str,
        observation: str,
        outcome: TraceOutcome | str = TraceOutcome.SUCCESS,
        session_id: str = "default",
        step_index: int = 0,
        error_context: str | None = None,
        tokens: int = 0,
        salience: float = 0.5,
        parent_episode_id: str | None = None,
        tags: list[str] | None = None,
        trace_id: str | None = None,
        timestamp: float | None = None,
    ) -> EpisodicTrace:
        """Record an immediate turn-by-turn trace."""
        if isinstance(outcome, str):
            try:
                outcome = TraceOutcome(outcome.lower())
            except ValueError:
                outcome = TraceOutcome.UNKNOWN

        # Auto-boost salience on failures for learning
        if outcome == TraceOutcome.FAILURE and salience < 0.7:
            salience = 0.8

        trace = EpisodicTrace(
            action=action.strip(),
            observation=observation.strip(),
            outcome=outcome,
            session_id=session_id,
            step_index=step_index,
            error_context=error_context,
            tokens=tokens,
            salience=salience,
            parent_episode_id=parent_episode_id,
            timestamp=timestamp or time.time(),
            tags=tags or [],
        )
        if trace_id:
            trace.trace_id = trace_id

        self._traces[trace.trace_id] = trace
        self._enforce_trace_capacity()
        return trace

    def get_trace(self, trace_id: str) -> EpisodicTrace | None:
        return self._traces.get(trace_id)

    def delete_trace(self, trace_id: str) -> bool:
        if trace_id in self._traces:
            del self._traces[trace_id]
            return True
        return False

    def list_traces(
        self,
        session_id: str | None = None,
        outcome: TraceOutcome | None = None,
        limit: int = 50,
    ) -> list[EpisodicTrace]:
        """List traces sorted by timestamp descending."""
        traces = list(self._traces.values())
        if session_id:
            traces = [t for t in traces if t.session_id == session_id]
        if outcome:
            traces = [t for t in traces if t.outcome == outcome]
        traces.sort(key=lambda t: t.timestamp, reverse=True)
        return traces[:limit]

    def create_hierarchical_episode(
        self,
        title: str,
        summary: str,
        session_id: str = "default",
        trace_ids: list[str] | None = None,
        key_learnings: list[str] | None = None,
        importance: float = 0.7,
        episode_id: str | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> HierarchicalEpisode:
        """Synthesize a sequence of traces into an abstract hierarchical episode."""
        now = time.time()
        s_time = start_time or now
        trace_ids = trace_ids or []
        if trace_ids and start_time is None:
            found_traces = [self._traces[tid] for tid in trace_ids if tid in self._traces]
            if found_traces:
                s_time = min(t.timestamp for t in found_traces)

        episode = HierarchicalEpisode(
            title=title.strip(),
            summary=summary.strip(),
            session_id=session_id,
            traces=trace_ids,
            key_learnings=key_learnings or [],
            importance=max(0.0, min(1.0, importance)),
            start_time=s_time,
            end_time=end_time or now,
        )
        if episode_id:
            episode.episode_id = episode_id

        for tid in trace_ids:
            if tid in self._traces:
                self._traces[tid].parent_episode_id = episode.episode_id

        self._episodes[episode.episode_id] = episode
        self._enforce_episode_capacity()
        return episode

    def get_episode(self, episode_id: str) -> HierarchicalEpisode | None:
        return self._episodes.get(episode_id)

    def delete_episode(self, episode_id: str) -> bool:
        if episode_id in self._episodes:
            del self._episodes[episode_id]
            return True
        return False

    def list_episodes(
        self,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[HierarchicalEpisode]:
        """List hierarchical episodes sorted by importance and recency."""
        eps = list(self._episodes.values())
        if session_id:
            eps = [e for e in eps if e.session_id == session_id]
        eps.sort(key=lambda e: (e.importance, e.end_time), reverse=True)
        return eps[:limit]

    def _enforce_trace_capacity(self) -> None:
        if len(self._traces) <= self.max_flat_traces:
            return
        sorted_keys = sorted(self._traces.keys(), key=lambda k: self._traces[k].timestamp)
        excess = len(self._traces) - self.max_flat_traces
        for k in sorted_keys[:excess]:
            del self._traces[k]

    def _enforce_episode_capacity(self) -> None:
        if len(self._episodes) <= self.max_episodes:
            return
        sorted_keys = sorted(
            self._episodes.keys(),
            key=lambda k: (self._episodes[k].importance, self._episodes[k].end_time),
        )
        excess = len(self._episodes) - self.max_episodes
        for k in sorted_keys[:excess]:
            del self._episodes[k]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_traces": len(self._traces),
            "total_episodes": len(self._episodes),
            "episodes": [e.to_dict() for e in self.list_episodes(limit=20)],
            "recent_traces": [t.to_dict() for t in self.list_traces(limit=20)],
        }
