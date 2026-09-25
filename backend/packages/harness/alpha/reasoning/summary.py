"""User-facing reasoning summary and the section 24 private-reasoning guard.

The summary is built only from typed structured records.  It never asks a model
to narrate its reasoning and never treats a provider thinking block, hidden
reasoning token stream, internal monologue, credential-bearing field, or
unbounded chain log as persistable explanation.

``assert_no_private_reasoning`` is the fail-closed enforcement point used by the
guarded persistence helper.  It is intentionally conservative: a payload that
cannot be proven safe is rejected rather than heuristically scrubbed.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from alpha.reasoning.models import ClampedModel, ReasoningState, ReasoningStatus, VerificationStatus

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from alpha.reasoning.config import ReasoningConfig

__all__ = [
    "MemoryLessonHandoff",
    "PrivateReasoningRejected",
    "PrivateReasoningViolation",
    "ReasoningSummary",
    "SafeReasoningRecord",
    "SafeRecordKind",
    "SummarySettings",
    "UnsafePersistencePayload",
    "assert_no_private_reasoning",
    "build_safe_reasoning_record",
    "generate_reasoning_summary",
    "redact_private_reasoning",
    "validated_lesson_handoffs",
    "write_safe_reasoning_payload",
]


class SafeRecordKind(StrEnum):
    """The ten legitimate durable reasoning record classes."""

    GOAL = "goal"
    PLAN = "plan"
    DECISION = "decision"
    OBSERVATION_SUMMARY = "observation_summary"
    EVIDENCE = "evidence"
    VERIFICATION = "verification"
    FAILURE_REASON = "failure_reason"
    REFLECTION_LESSON = "reflection_lesson"
    ARTIFACT_PROVENANCE = "artifact_provenance"
    USER_VISIBLE_SUMMARY = "user_visible_summary"


class PrivateReasoningViolation(StrEnum):
    RAW_HIDDEN_REASONING = "raw_hidden_reasoning"
    UNFILTERED_MONOLOGUE = "unfiltered_monologue"
    PROVIDER_THINKING_BLOCK = "provider_thinking_block"
    CREDENTIAL_CONTENT = "credential_content"
    UNBOUNDED_CHAIN_LOG = "unbounded_chain_log"


class PrivateReasoningRejected(ValueError):
    """A payload cannot cross the reasoning-plane persistence boundary."""

    def __init__(self, violation: PrivateReasoningViolation, location: str) -> None:
        super().__init__(f"private reasoning payload rejected: {violation.value} at {location}")
        self.violation = violation
        self.location = location


class UnsafePersistencePayload(ValueError):
    """A structurally safe payload is not an approved persistence type."""


class SummarySettings(ClampedModel):
    max_items: int = Field(default=8)
    max_chars: int = Field(default=4000)
    include_evidence: bool = True
    include_uncertainty: bool = True
    include_reflections: bool = True
    max_payload_nodes: int = Field(default=10_000)
    max_payload_bytes: int = Field(default=1_000_000)

    @model_validator(mode="after")
    def _clamp_values(self) -> SummarySettings:
        self.clamp_field("max_items", 1, 100, integer=True)
        self.clamp_field("max_chars", 256, 100_000, integer=True)
        self.clamp_field("max_payload_nodes", 100, 1_000_000, integer=True)
        self.clamp_field("max_payload_bytes", 1024, 100_000_000, integer=True)
        return self


class SafeReasoningRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: f"reasoning-record-{uuid.uuid4().hex}", min_length=1, max_length=128)
    record_kind: SafeRecordKind
    payload: dict[str, JsonValue] = Field(max_length=64)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_payload(self) -> SafeReasoningRecord:
        if not self.payload:
            raise ValueError("a safe reasoning record requires at least one structured field")
        return self


class ReasoningSummary(BaseModel):
    what_was_done: list[str] = Field(default_factory=list, max_length=100)
    key_findings: list[str] = Field(default_factory=list, max_length=100)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    verification: list[str] = Field(default_factory=list, max_length=100)
    uncertainty_and_limitations: list[str] = Field(default_factory=list, max_length=100)
    result: str = Field(min_length=1, max_length=100_000)
    source_record_ids: list[str] = Field(default_factory=list, max_length=500)

    model_config = ConfigDict(extra="forbid")


class MemoryLessonHandoff(BaseModel):
    """A lesson that crossed the evidence gate and may be offered to memory."""

    reflection_id: str
    lesson: str
    evidence_ids: list[str]
    verification_ids: list[str]
    independent_verification: bool

    model_config = ConfigDict(extra="forbid", frozen=True)


_HIDDEN_REASONING_KEYS = {
    "chain_of_thought",
    "raw_reasoning",
    "raw_thoughts",
    "hidden_reasoning",
    "private_reasoning",
    "reasoning_tokens",
    "thinking_tokens",
    "reasoning_content",
    "raw_response_thinking",
}
_MONOLOGUE_KEYS = {
    "monologue",
    "internal_monologue",
    "inner_monologue",
    "scratchpad",
    "thought",
    "thoughts",
    "thought_trace",
    "reasoning_trace",
    "reasoning_steps",
}
_PROVIDER_THINKING_KEYS = {
    "thinking",
    "thinking_blocks",
    "thinking_block",
    "redacted_thinking",
    "encrypted_thinking",
    "provider_thinking",
    "signature_thinking",
}
_CHAIN_LOG_KEYS = {
    "chain_log",
    "reasoning_chain",
    "intermediate_chain",
    "intermediate_steps",
    "trajectory",
    "raw_steps",
    "scratch_log",
    "message_chain",
    "full_history",
}
_CREDENTIAL_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "client_secret",
    "password",
    "passwd",
    "secret",
    "credential",
    "credentials",
    "private_key",
    "token",
}
_RAW_MARKERS = (
    "<think>",
    "<thinking>",
    "<reasoning>",
    "internal monologue:",
    "raw chain-of-thought:",
    "chain-of-thought:",
    "hidden reasoning:",
    "private reasoning:",
)
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._-]{8,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|password|passwd|secret|access[_-]?token)\s*[=:]\s*\S+"),
    re.compile(r"(?i)[?&](?:api[_-]?key|access[_-]?token|token|key)=[^&\s]+"),
)


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().casefold()).strip("_")


def _violation_for_text(value: str) -> PrivateReasoningViolation | None:
    lowered = value.casefold()
    if any(marker in lowered for marker in _RAW_MARKERS):
        return PrivateReasoningViolation.RAW_HIDDEN_REASONING
    if any(pattern.search(value) for pattern in _CREDENTIAL_PATTERNS):
        return PrivateReasoningViolation.CREDENTIAL_CONTENT
    return None


def assert_no_private_reasoning(payload: Any, *, settings: SummarySettings | None = None) -> None:
    """Reject every prohibited payload class recursively.

    The function is structural and side-effect free.  It never returns a
    partially scrubbed payload: callers either receive the original value or a
    :class:`PrivateReasoningRejected` exception.
    """

    resolved = settings or SummarySettings()
    stack: list[tuple[Any, str]] = [(payload, "$")]
    visited = 0
    while stack:
        current, location = stack.pop()
        visited += 1
        if visited > resolved.max_payload_nodes:
            raise PrivateReasoningRejected(PrivateReasoningViolation.UNBOUNDED_CHAIN_LOG, location)

        if isinstance(current, BaseModel):
            stack.append((current.model_dump(mode="json"), location))
            continue
        if isinstance(current, Mapping):
            normalized = {_normalized_key(key): value for key, value in current.items()}
            if (
                "type" in normalized
                and str(normalized["type"]).casefold()
                in {
                    "thinking",
                    "redacted_thinking",
                    "reasoning",
                }
                and any(key in normalized for key in ("data", "thinking", "signature", "content"))
            ):
                raise PrivateReasoningRejected(PrivateReasoningViolation.PROVIDER_THINKING_BLOCK, location)
            for key, value in current.items():
                key_name = _normalized_key(key)
                child_location = f"{location}.{key_name}"
                if key_name in _HIDDEN_REASONING_KEYS:
                    raise PrivateReasoningRejected(PrivateReasoningViolation.RAW_HIDDEN_REASONING, child_location)
                if key_name in _MONOLOGUE_KEYS:
                    raise PrivateReasoningRejected(PrivateReasoningViolation.UNFILTERED_MONOLOGUE, child_location)
                if key_name in _PROVIDER_THINKING_KEYS:
                    raise PrivateReasoningRejected(PrivateReasoningViolation.PROVIDER_THINKING_BLOCK, child_location)
                if key_name in _CHAIN_LOG_KEYS:
                    raise PrivateReasoningRejected(PrivateReasoningViolation.UNBOUNDED_CHAIN_LOG, child_location)
                if key_name in _CREDENTIAL_KEYS:
                    raise PrivateReasoningRejected(PrivateReasoningViolation.CREDENTIAL_CONTENT, child_location)
                stack.append((value, child_location))
            continue
        if isinstance(current, (list, tuple, set)):
            for index, value in enumerate(current):
                stack.append((value, f"{location}[{index}]"))
            continue
        if isinstance(current, str):
            violation = _violation_for_text(current)
            if violation is not None:
                raise PrivateReasoningRejected(violation, location)


def build_safe_reasoning_record(
    record_kind: SafeRecordKind,
    payload: Mapping[str, JsonValue],
) -> SafeReasoningRecord:
    """Validate one of the ten legitimate durable record classes."""

    record = SafeReasoningRecord(record_kind=record_kind, payload=dict(payload))
    assert_no_private_reasoning(record)
    return record


def _bounded_list(values: Sequence[str], limit: int) -> list[str]:
    if len(values) <= limit:
        return list(values)
    omitted = len(values) - limit
    return [*values[:limit], f"{omitted} additional structured record(s) omitted by the configured summary limit."]


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    suffix = " … [disclosed truncation]"
    return value[: max(0, limit - len(suffix))] + suffix


def generate_reasoning_summary(
    state: ReasoningState,
    *,
    config: ReasoningConfig | None = None,
    settings: SummarySettings | None = None,
) -> ReasoningSummary:
    """Build a user-facing explanation from structured records only."""

    from alpha.reasoning.config import ReasoningConfig

    resolved_config = config or ReasoningConfig()
    resolved = settings or resolved_config.summary
    source_ids: list[str] = []

    done = [f"Completed step: {step}" for step in state.completed_step_ids]
    done.extend(f"Decision: {decision.selected}" for decision in state.decision_log)
    if not done:
        done = ["No completed plan step or decision was recorded."]

    findings = [f"Observation: {observation.summary}" for observation in state.observations]
    if resolved.include_reflections:
        findings.extend(f"Reflection lesson: {reflection.lesson}" for reflection in state.reflection_events)
    findings.extend(f"Artifact provenance: {provenance}" for provenance in state.artifact_provenance)
    for observation in state.observations:
        source_ids.append(observation.id)
    for reflection in state.reflection_events:
        source_ids.append(reflection.id)
    for decision in state.decision_log:
        source_ids.append(decision.id)

    evidence_lines: list[str] = []
    if resolved.include_evidence:
        for evidence in state.evidence:
            verification = "verified" if evidence.verified else "not independently verified"
            evidence_lines.append(f"{evidence.claim} — {evidence.source} ({verification})")
            source_ids.append(evidence.id)

    verification_lines = [f"{record.target} — {record.method}: {record.status.value.upper()}" + (f" ({record.failure_reason})" if record.failure_reason else "") for record in state.verifications]
    for record in state.verifications:
        source_ids.extend(record.evidence_ids)
        source_ids.append(record.id)

    limitations: list[str] = [f"Open question: {question}" for question in state.unresolved_questions]
    limitations.extend(f"Assumption: {assumption}" for assumption in state.assumptions)
    if state.confidence is not None:
        limitations.append(f"Declared confidence={state.confidence.value:.2f} ({state.confidence.calibration_status.value}; {state.confidence.method})")
    if resolved.include_uncertainty and state.uncertainty is not None:
        for dimension, estimate in state.uncertainty.dominant_dimensions():
            limitations.append(f"{dimension.value} uncertainty={estimate.value:.2f} ({estimate.calibration_status.value}; {estimate.method})")
    limitations.extend(f"Unresolved contradiction: {record.claim_a} vs {record.claim_b}" for record in state.contradictions if record.resolution.value == "open")
    if state.failure_reason:
        limitations.append(f"Failure reason: {state.failure_reason}")
    if state.stop_reason is not None:
        limitations.append(f"Stop reason recorded by runtime: {state.stop_reason.value}")
    if not limitations:
        limitations = ["No additional uncertainty or limitation was recorded."]

    if state.result:
        result = state.result
    elif state.status is ReasoningStatus.COMPLETED:
        result = "The existing runtime recorded completion; this package supplies no independent success claim."
    elif state.status in {ReasoningStatus.BLOCKED, ReasoningStatus.FAILED}:
        result = f"The existing runtime recorded {state.status.value}; no successful result was asserted."
    else:
        result = "No final result has been recorded."

    limit = resolved.max_items
    summary = ReasoningSummary(
        what_was_done=[_truncate(value, resolved.max_chars) for value in _bounded_list(done, limit)],
        key_findings=[_truncate(value, resolved.max_chars) for value in _bounded_list(findings, limit)],
        evidence=[_truncate(value, resolved.max_chars) for value in _bounded_list(evidence_lines, limit)],
        verification=[_truncate(value, resolved.max_chars) for value in _bounded_list(verification_lines, limit)],
        uncertainty_and_limitations=[_truncate(value, resolved.max_chars) for value in _bounded_list(limitations, limit)],
        result=_truncate(result, resolved.max_chars),
        source_record_ids=list(dict.fromkeys(source_ids))[:500],
    )
    assert_no_private_reasoning(summary, settings=resolved)
    return summary


def redact_private_reasoning(
    payload: ReasoningState | ReasoningSummary | SafeReasoningRecord,
    *,
    config: ReasoningConfig | None = None,
) -> ReasoningSummary | SafeReasoningRecord:
    """Fail-closed redaction: reject unsafe input, project safe state to a summary."""

    assert_no_private_reasoning(payload)
    if isinstance(payload, ReasoningState):
        return generate_reasoning_summary(payload, config=config)
    if isinstance(payload, (ReasoningSummary, SafeReasoningRecord)):
        return payload.model_copy(deep=True)
    raise UnsafePersistencePayload("only ReasoningState, ReasoningSummary, and SafeReasoningRecord may be redacted")


def validated_lesson_handoffs(state: ReasoningState) -> tuple[MemoryLessonHandoff, ...]:
    """Return reflection lessons that crossed the existing evidence/verification gate.

    This is a hand-off proposal only.  It does not write memory, deduplicate
    lessons, or assert that a lesson is globally useful.
    """

    passing = [record for record in state.verifications if record.status is VerificationStatus.PASS]
    handoffs: list[MemoryLessonHandoff] = []
    for reflection in state.reflection_events:
        if not reflection.should_persist_to_memory or not reflection.evidence_ids:
            continue
        matched = [record for record in passing if set(record.evidence_ids).intersection(reflection.evidence_ids)]
        if not matched:
            continue
        handoffs.append(
            MemoryLessonHandoff(
                reflection_id=reflection.id,
                lesson=reflection.lesson,
                evidence_ids=list(reflection.evidence_ids),
                verification_ids=[record.id for record in matched],
                independent_verification=any(record.independent for record in matched),
            )
        )
    return tuple(handoffs)


def write_safe_reasoning_payload(
    path: Path | str | None,
    payload: ReasoningState | ReasoningSummary | SafeReasoningRecord,
    *,
    config: ReasoningConfig | None = None,
) -> Path:
    """Atomically persist a bounded, structured reasoning payload.

    The guard runs before the destination directory is created, so a rejected
    payload cannot leave a partial file behind.
    """

    from alpha.reasoning.config import ReasoningConfig

    resolved_config = config or ReasoningConfig()
    settings = resolved_config.summary
    assert_no_private_reasoning(payload, settings=settings)
    if not isinstance(payload, (ReasoningState, ReasoningSummary, SafeReasoningRecord)):
        raise UnsafePersistencePayload("payload is not an approved structured reasoning type")

    destination = Path(path) if path is not None else resolved_config.storage_path
    if destination is None:
        raise UnsafePersistencePayload("no reasoning storage_path is configured")
    destination = destination.expanduser().resolve(strict=False)
    serialized = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
    encoded = serialized.encode("utf-8")
    if len(encoded) > settings.max_payload_bytes:
        raise UnsafePersistencePayload(f"reasoning payload is {len(encoded)} bytes, exceeding max_payload_bytes={settings.max_payload_bytes}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
