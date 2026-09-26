"""Deterministic retrieval-mode selection and per-call task context."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import RetrievalMode, TemporalOperation

_MODE_VALUES = frozenset({"exact", "semantic", "graph", "temporal", "procedural", "hybrid"})

_EXACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("callable_or_api_name", re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*\(")),
    ("environment_variable", re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")),
    ("camel_case_identifier", re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+\b")),
    ("filename_or_path", re.compile(r"(?:\b[\w.-]+[/\\])+\b[\w.-]+|\b[\w-]+\.(?:py|md|ya?ml|json|toml|tsx?|jsx?|log|txt)\b", re.IGNORECASE)),
    ("version_number", re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b", re.IGNORECASE)),
    ("opaque_id", re.compile(r"\b[a-f0-9]{12,}\b", re.IGNORECASE)),
    ("error_string", re.compile(r"\b(?:error|exception|traceback|stack trace|failed with)\b", re.IGNORECASE)),
)

_TEMPORAL_PATTERN = re.compile(
    r"\b(?:latest|current(?:ly)?|as of|changed|change(?:d)?|recent(?:ly)?|last (?:week|month|quarter|year)|yesterday|today|tomorrow|history|timeline)\b",
    re.IGNORECASE,
)
_PRIOR_BELIEF_PATTERN = re.compile(r"\bwhat did (?:we|you|the system) believe before\b", re.IGNORECASE)
_PROCEDURAL_PATTERN = re.compile(
    r"\b(?:how did we solve|how do i solve|which workflow|what workflow|tool sequence|which tool|step[- ]by[- ]step|playbook|procedure|trajectory|worked before|solve this|fix(?:ed)? (?:this|it|last time))\b",
    re.IGNORECASE,
)
_GRAPH_PATTERN = re.compile(
    r"\b(?:depend(?:s|ency|encies)?(?:\s+on)?|dependencies|connected to|relationship between|related to|multi[- ]hop|neighbou?rs?|which agent|who knows|entity graph|knowledge graph)\b",
    re.IGNORECASE,
)


class TaskContext(BaseModel):
    """Scope, task, budget, and mode-specific bounds for one recall."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str = Field(min_length=1, max_length=512)
    task_type: str = Field(min_length=1, max_length=128)
    budget: int = Field(ge=1, le=1_000_000)
    max_candidates: int = Field(default=50, ge=1, le=1000)
    agent_name: str | None = Field(default=None, max_length=128)
    as_of: float | None = Field(default=None, ge=0.0)
    temporal_operation: TemporalOperation = "latest"
    window_start: float | None = Field(default=None, ge=0.0)
    window_end: float | None = Field(default=None, ge=0.0)
    graph_seed_ids: tuple[str, ...] = ()
    graph_max_depth: int = Field(default=2, ge=1, le=3)
    graph_max_nodes: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def _temporal_bounds_are_explicit(self) -> TaskContext:
        if self.temporal_operation == "changed_in_window" and (self.window_start is None or self.window_end is None):
            raise ValueError("changed_in_window requires window_start and window_end")
        if self.window_start is not None and self.window_end is not None and self.window_start > self.window_end:
            raise ValueError("window_start must not exceed window_end")
        return self


class ModeSelection(BaseModel):
    """Selected mode plus the disclosed reason and matched query signals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: RetrievalMode
    reason: str = Field(min_length=1)
    matched_signals: tuple[str, ...] = ()


def select_mode(query: str, explicit_intent: RetrievalMode | None = None) -> ModeSelection:
    """Select one retrieval mode with deterministic precedence.

    Explicit caller intent always wins. Without intent, exact identifiers are
    checked first. Two or more distinct temporal/procedural/graph cue families
    select ``hybrid``; a single cue family selects that mode; all other text is
    semantic. The reason always identifies whether selection was explicit or
    evidence-driven.
    """
    if explicit_intent is not None:
        if explicit_intent not in _MODE_VALUES:
            raise ValueError(f"unknown retrieval mode: {explicit_intent}")
        return ModeSelection(
            mode=explicit_intent,
            reason=f"explicit_caller_intent:{explicit_intent}",
            matched_signals=("explicit_intent",),
        )

    text = (query or "").strip()
    if not text:
        return ModeSelection(mode="semantic", reason="empty_query_defaults_to_semantic")

    exact_signals = [name for name, pattern in _EXACT_PATTERNS if pattern.search(text)]
    if exact_signals:
        return ModeSelection(
            mode="exact",
            reason="exact_identifier_or_error_signal",
            matched_signals=tuple(exact_signals),
        )

    cue_modes: list[tuple[RetrievalMode, str]] = []
    if _TEMPORAL_PATTERN.search(text) or _PRIOR_BELIEF_PATTERN.search(text):
        cue_modes.append(("temporal", "temporal_time_signal"))
    if _PROCEDURAL_PATTERN.search(text):
        cue_modes.append(("procedural", "procedural_workflow_signal"))
    if _GRAPH_PATTERN.search(text):
        cue_modes.append(("graph", "graph_relationship_signal"))

    unique_modes = {mode for mode, _signal in cue_modes}
    if len(unique_modes) > 1:
        return ModeSelection(
            mode="hybrid",
            reason="multiple_mode_signals",
            matched_signals=tuple(signal for _mode, signal in cue_modes),
        )
    if cue_modes:
        mode, signal = cue_modes[0]
        return ModeSelection(mode=mode, reason=f"single_{mode}_signal", matched_signals=(signal,))

    return ModeSelection(
        mode="semantic",
        reason="conceptual_or_paraphrase_query",
        matched_signals=("semantic_default",),
    )


__all__ = ["ModeSelection", "TaskContext", "select_mode"]
