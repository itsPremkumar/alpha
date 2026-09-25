"""Data contracts for Alpha's deterministic memory evaluation harness.

The case shape follows plan section 28: a case has ordered sessions, each
session has events, and a case has questions with expected answers.  The
harness adds a small amount of explicit metadata so a deterministic runner can
measure storage, scope, provenance, and token cost without calling an LLM.

No model in this module performs I/O.  ``Case.from_dict`` is intentionally
strict about the closed ability vocabulary, while accepting the spelling
variants used by the five LongMemEval dimensions (for example,
``knowledge-update`` and ``knowledge_update``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Ability = Literal[
    "extraction",
    "multi_session",
    "temporal",
    "update",
    "abstention",
    "procedural",
    "failure_avoidance",
    "contamination",
    "write_precision",
    "evidence_traceability",
    "token_efficiency",
]
ResultStatus = Literal["pass", "fail", "unavailable", "error"]

#: Keep this order stable in reports and machine-readable output.
ABILITIES: tuple[Ability, ...] = (
    "extraction",
    "multi_session",
    "temporal",
    "update",
    "abstention",
    "procedural",
    "failure_avoidance",
    "contamination",
    "write_precision",
    "evidence_traceability",
    "token_efficiency",
)
KNOWN_ABILITIES: frozenset[str] = frozenset(ABILITIES)
METRIC_NAMES: tuple[str, ...] = (
    "extraction_recall",
    "multi_session_accuracy",
    "temporal_accuracy",
    "update_accuracy",
    "abstention_rate",
    "contamination_rate",
    "write_precision",
    "evidence_traceability",
    "token_efficiency",
)
DEFAULT_NOW = "2026-01-15T00:00:00Z"

_ABILITY_ALIASES: dict[str, Ability] = {
    "extraction": "extraction",
    "information_extraction": "extraction",
    "information-extraction": "extraction",
    "multi_session": "multi_session",
    "multi-session": "multi_session",
    "multi_session_reasoning": "multi_session",
    "multi-session-reasoning": "multi_session",
    "temporal": "temporal",
    "temporal_reasoning": "temporal",
    "temporal-reasoning": "temporal",
    "update": "update",
    "knowledge_update": "update",
    "knowledge-update": "update",
    "abstention": "abstention",
    "procedural": "procedural",
    "procedural_reuse": "procedural",
    "procedural-reuse": "procedural",
    "failure_avoidance": "failure_avoidance",
    "failure-avoidance": "failure_avoidance",
    "contamination": "contamination",
    "memory_contamination": "contamination",
    "memory-contamination": "contamination",
    "write_precision": "write_precision",
    "write-precision": "write_precision",
    "evidence_traceability": "evidence_traceability",
    "evidence-traceability": "evidence_traceability",
    "token_efficiency": "token_efficiency",
    "token-efficiency": "token_efficiency",
}

_QUESTION_TYPE_ALIASES: dict[str, str] = {
    "information_extraction": "information_extraction",
    "information-extraction": "information_extraction",
    "extraction": "information_extraction",
    "multi_session_reasoning": "multi_session_reasoning",
    "multi-session-reasoning": "multi_session_reasoning",
    "multi_session": "multi_session_reasoning",
    "temporal_reasoning": "temporal_reasoning",
    "temporal-reasoning": "temporal_reasoning",
    "temporal": "temporal_reasoning",
    "knowledge_update": "knowledge_update",
    "knowledge-update": "knowledge_update",
    "update": "knowledge_update",
    "abstention": "abstention",
    "abstention_required": "abstention",
    "abstention-required": "abstention",
    "procedural": "procedural",
    "procedural_reuse": "procedural",
    "failure_avoidance": "failure_avoidance",
    "evidence_traceability": "evidence_traceability",
    "write_precision": "write_precision",
    "contamination": "contamination",
    "token_efficiency": "token_efficiency",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value if item is not None and str(item).strip())
    return (str(value),)


def _stable_id(prefix: str, value: str, index: int = 0) -> str:
    digest = hashlib.sha256(f"{index}|{value}".encode("utf-8", "strict")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def normalize_ability(value: str) -> Ability:
    """Return the canonical closed-vocabulary ability name."""

    normalized = str(value).strip().lower().replace(" ", "_")
    try:
        return _ABILITY_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown memory evaluation ability: {value!r}") from exc


def _normalize_question_type(value: Any) -> str:
    normalized = str(value or "question").strip().lower().replace(" ", "_")
    return _QUESTION_TYPE_ALIASES.get(normalized, normalized)


def _merge_scope(*values: Mapping[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if value:
            merged.update(dict(value))
    return merged


@dataclass
class Event:
    """One deterministic input event supplied to a provider's ``write``."""

    id: str = ""
    text: str = ""
    session_id: str = ""
    timestamp: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)
    expected_stored: bool | None = None
    answer: str | None = None
    fact_id: str | None = None
    memory_type: str = "episodic"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = str(self.id or _stable_id("event", f"{self.session_id}|{self.text}", len(self.metadata)))
        self.text = str(self.text)
        self.session_id = str(self.session_id)
        self.scope = _mapping(self.scope)
        self.metadata = _mapping(self.metadata)
        if self.timestamp is not None:
            self.timestamp = str(self.timestamp)
        if self.answer is not None:
            self.answer = str(self.answer)
        if self.fact_id is not None:
            self.fact_id = str(self.fact_id)
        if self.expected_stored is not None:
            self.expected_stored = bool(self.expected_stored)

    @classmethod
    def from_value(cls, value: Any, *, session_id: str = "", index: int = 0) -> Event:
        if isinstance(value, cls):
            if not value.session_id:
                value.session_id = session_id
            return value
        if isinstance(value, str):
            return cls(id=_stable_id("event", f"{session_id}|{value}", index), text=value, session_id=session_id)
        if not isinstance(value, Mapping):
            raise ValueError(f"event must be a string or mapping, got {type(value).__name__}")
        data = dict(value)
        text = data.get("text", data.get("content", data.get("event", data.get("value", ""))))
        event_id = str(data.get("id", data.get("event_id", "")) or _stable_id("event", f"{session_id}|{text}", index))
        known = {
            "id",
            "event_id",
            "text",
            "content",
            "event",
            "value",
            "session_id",
            "timestamp",
            "at",
            "occurred_at",
            "scope",
            "expected_stored",
            "answer",
            "fact_id",
            "memory_type",
            "type",
        }
        metadata = {key: val for key, val in data.items() if key not in known}
        return cls(
            id=event_id,
            text=str(text),
            session_id=str(data.get("session_id", session_id)),
            timestamp=(str(data["timestamp"]) if data.get("timestamp") is not None else data.get("at", data.get("occurred_at"))),
            scope=_mapping(data.get("scope")),
            expected_stored=(bool(data["expected_stored"]) if "expected_stored" in data else None),
            answer=(str(data["answer"]) if data.get("answer") is not None else None),
            fact_id=(str(data["fact_id"]) if data.get("fact_id") is not None else None),
            memory_type=str(data.get("memory_type", data.get("type", "episodic"))),
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "scope": dict(self.scope),
            "expected_stored": self.expected_stored,
            "answer": self.answer,
            "fact_id": self.fact_id,
            "memory_type": self.memory_type,
            "metadata": dict(self.metadata),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def keys(self) -> Any:
        return self.to_dict().keys()

    def items(self) -> Any:
        return self.to_dict().items()

    def __iter__(self) -> Any:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


@dataclass
class Session:
    """An ordered group of events from one conversation/session."""

    session_id: str
    events: tuple[Event, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.session_id = str(self.session_id)
        self.events = tuple(Event.from_value(event, session_id=self.session_id, index=index) for index, event in enumerate(self.events))
        self.metadata = _mapping(self.metadata)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, index: int = 0) -> Session:
        data = dict(value)
        session_id = str(data.get("session_id", data.get("id", f"s{index + 1}")))
        events = tuple(Event.from_value(event, session_id=session_id, index=event_index) for event_index, event in enumerate(data.get("events", ())))
        return cls(session_id=session_id, events=events, metadata=_mapping(data.get("metadata")))

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "events": [event.to_dict() for event in self.events], "metadata": dict(self.metadata)}


@dataclass
class Question:
    """A question and its deterministic grading contract."""

    id: str
    question: str
    expected: str | None = None
    type: str = "question"
    acceptable_alternatives: tuple[str, ...] = ()
    expects_abstention: bool = False
    evidence_ids: tuple[str, ...] = ()
    superseded_values: tuple[str, ...] = ()
    scope: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = str(self.id)
        self.question = str(self.question)
        self.expected = None if self.expected is None else str(self.expected)
        self.type = _normalize_question_type(self.type)
        self.acceptable_alternatives = _string_tuple(self.acceptable_alternatives)
        self.expects_abstention = bool(self.expects_abstention)
        self.evidence_ids = _string_tuple(self.evidence_ids)
        self.superseded_values = _string_tuple(self.superseded_values)
        self.scope = _mapping(self.scope) if self.scope is not None else None
        self.metadata = _mapping(self.metadata)

    @property
    def is_update(self) -> bool:
        return self.type == "knowledge_update"

    @property
    def answer(self) -> str | None:
        return self.expected

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, index: int = 0) -> Question:
        data = dict(value)
        question_text = str(data.get("question", data.get("prompt", "")))
        question_id = str(data.get("id", data.get("question_id", f"q{index + 1}")))
        expected_value = data.get("expected", data.get("answer"))
        alternatives = data.get("acceptable_alternatives", data.get("acceptable_answers", data.get("alternatives", ())))
        evidence = data.get("evidence_ids", data.get("evidence", data.get("supporting_event_ids", ())))
        superseded = data.get("superseded_values", data.get("superseded", data.get("old_values", ())))
        if isinstance(superseded, Mapping):
            superseded = superseded.get("values", superseded.get("value", ()))
        metadata = _mapping(data.get("metadata"))
        question_type = data.get("type", data.get("question_type", "question"))
        expects = data.get("expects_abstention")
        if expects is None:
            expects = metadata.get("expects_abstention", _normalize_question_type(question_type) == "abstention")
        known = {
            "id",
            "question_id",
            "question",
            "prompt",
            "expected",
            "answer",
            "type",
            "question_type",
            "acceptable_alternatives",
            "acceptable_answers",
            "alternatives",
            "expects_abstention",
            "evidence_ids",
            "evidence",
            "supporting_event_ids",
            "superseded_values",
            "superseded",
            "old_values",
            "scope",
            "metadata",
        }
        metadata.update({key: val for key, val in data.items() if key not in known})
        return cls(
            id=question_id,
            question=question_text,
            expected=None if expected_value is None else str(expected_value),
            type=str(question_type),
            acceptable_alternatives=_string_tuple(alternatives),
            expects_abstention=bool(expects),
            evidence_ids=_string_tuple(evidence),
            superseded_values=_string_tuple(superseded),
            scope=_mapping(data.get("scope")) if data.get("scope") is not None else None,
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "expected": self.expected,
            "type": self.type,
            "acceptable_alternatives": list(self.acceptable_alternatives),
            "expects_abstention": self.expects_abstention,
            "evidence_ids": list(self.evidence_ids),
            "superseded_values": list(self.superseded_values),
            "scope": dict(self.scope) if self.scope is not None else None,
            "metadata": dict(self.metadata),
        }


@dataclass
class Case:
    """A complete original, human-readable memory benchmark case."""

    id: str
    ability: Ability
    sessions: tuple[Session, ...] = ()
    questions: tuple[Question, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)
    now: str = DEFAULT_NOW

    def __post_init__(self) -> None:
        self.id = str(self.id)
        if not self.id:
            raise ValueError("case id must not be empty")
        self.ability = normalize_ability(self.ability)
        self.sessions = tuple(session if isinstance(session, Session) else Session.from_dict(session, index=index) for index, session in enumerate(self.sessions))
        self.questions = tuple(question if isinstance(question, Question) else Question.from_dict(question, index=index) for index, question in enumerate(self.questions))
        self.metadata = _mapping(self.metadata)
        self.scope = _merge_scope(self.scope, self.metadata.get("scope") if isinstance(self.metadata.get("scope"), Mapping) else None)
        if not self.scope:
            self.scope = {"user_id": f"case-{self.id}", "agent_name": "alpha-memory-evaluation"}
        self.now = str(self.metadata.get("now", self.metadata.get("as_of", self.now or DEFAULT_NOW)))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Case:
        data = dict(value)
        metadata = _mapping(data.get("metadata"))
        ability = data.get("ability", metadata.get("ability"))
        if ability is None:
            raise ValueError(f"case {data.get('id', '<unknown>')!r} is missing ability")
        sessions = tuple(Session.from_dict(session, index=index) for index, session in enumerate(data.get("sessions", ())))
        questions = tuple(Question.from_dict(question, index=index) for index, question in enumerate(data.get("questions", ())))
        return cls(
            id=str(data.get("id", data.get("case_id", ""))),
            ability=normalize_ability(str(ability)),
            sessions=sessions,
            questions=questions,
            metadata=metadata,
            scope=_mapping(data.get("scope")),
            now=str(data.get("now", metadata.get("now", metadata.get("as_of", DEFAULT_NOW)))),
        )

    @classmethod
    def from_json(cls, value: str | Path) -> Case:
        import json

        raw = Path(value).read_text(encoding="utf-8") if isinstance(value, Path) else value
        payload = json.loads(raw)
        if not isinstance(payload, Mapping):
            raise ValueError("case JSON must contain an object")
        return cls.from_dict(payload)

    @property
    def case_id(self) -> str:
        return self.id

    @property
    def expected_stored_ids(self) -> tuple[str, ...]:
        explicit = self.metadata.get("expected_stored", self.metadata.get("expected_stored_ids"))
        if explicit is not None:
            return _string_tuple(explicit)
        return tuple(event.id for session in self.sessions for event in session.events if event.expected_stored is not False)

    @property
    def relevant_fact_ids(self) -> tuple[str, ...]:
        explicit = self.metadata.get("extraction_fact_ids", self.metadata.get("relevant_fact_ids", self.metadata.get("expected_fact_ids")))
        if explicit is not None:
            return _string_tuple(explicit)
        return self.expected_stored_ids

    @property
    def forget_ids(self) -> tuple[str, ...]:
        return _string_tuple(self.metadata.get("forget_ids", self.metadata.get("forget", ())))

    def events(self) -> tuple[Event, ...]:
        return tuple(event for session in self.sessions for event in session.events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ability": self.ability,
            "sessions": [session.to_dict() for session in self.sessions],
            "questions": [question.to_dict() for question in self.questions],
            "metadata": dict(self.metadata),
            "scope": dict(self.scope),
            "now": self.now,
        }

    def to_json(self, *, indent: int = 2) -> str:
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True)


@dataclass(frozen=True)
class MemoryRecord:
    """Normalized record returned by a memory provider's ``recall`` method."""

    id: str
    content: str = ""
    scope: dict[str, Any] = field(default_factory=dict)
    answer: str | None = None
    useful: bool = True
    cited: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "content", str(self.content))
        object.__setattr__(self, "scope", _mapping(self.scope))
        object.__setattr__(self, "answer", None if self.answer is None else str(self.answer))
        object.__setattr__(self, "useful", bool(self.useful))
        object.__setattr__(self, "cited", bool(self.cited))
        object.__setattr__(self, "metadata", _mapping(self.metadata))

    @property
    def event_id(self) -> str | None:
        value = self.metadata.get("event_id", self.metadata.get("source_event_id"))
        return None if value is None else str(value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "scope": dict(self.scope),
            "answer": self.answer,
            "useful": self.useful,
            "cited": self.cited,
            "metadata": dict(self.metadata),
        }


RecallRecord = MemoryRecord
Record = MemoryRecord


@dataclass
class QuestionOutcome:
    """One question's answer and the evidence that justified the grade."""

    question_id: str
    answer: str
    correct: bool
    expects_abstention: bool = False
    evidence_ids: tuple[str, ...] = ()
    returned_record_ids: tuple[str, ...] = ()
    foreign_evidence_ids: tuple[str, ...] = ()
    superseded_values: tuple[str, ...] = ()
    superseded_evidence_ids: tuple[str, ...] = ()
    update_correct: bool | None = None
    composed_tokens: int = 0
    useful_fact_count: int = 0
    failure_reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.question_id = str(self.question_id)
        self.answer = str(self.answer)
        self.correct = bool(self.correct)
        self.expects_abstention = bool(self.expects_abstention)
        self.evidence_ids = _string_tuple(self.evidence_ids)
        self.returned_record_ids = _string_tuple(self.returned_record_ids)
        self.foreign_evidence_ids = _string_tuple(self.foreign_evidence_ids)
        self.superseded_values = _string_tuple(self.superseded_values)
        self.superseded_evidence_ids = _string_tuple(self.superseded_evidence_ids)
        self.failure_reasons = _string_tuple(self.failure_reasons)
        self.metadata = _mapping(self.metadata)
        self.composed_tokens = max(0, int(self.composed_tokens))
        self.useful_fact_count = max(0, int(self.useful_fact_count))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> QuestionOutcome:
        data = dict(value)
        return cls(
            question_id=str(data.get("question_id", data.get("id", ""))),
            answer=str(data.get("answer", "")),
            correct=bool(data.get("correct", False)),
            expects_abstention=bool(data.get("expects_abstention", False)),
            evidence_ids=_string_tuple(data.get("evidence_ids", ())),
            returned_record_ids=_string_tuple(data.get("returned_record_ids", ())),
            foreign_evidence_ids=_string_tuple(data.get("foreign_evidence_ids", ())),
            superseded_values=_string_tuple(data.get("superseded_values", ())),
            superseded_evidence_ids=_string_tuple(data.get("superseded_evidence_ids", ())),
            update_correct=data.get("update_correct"),
            composed_tokens=int(data.get("composed_tokens", 0) or 0),
            useful_fact_count=int(data.get("useful_fact_count", 0) or 0),
            failure_reasons=_string_tuple(data.get("failure_reasons", ())),
            metadata=_mapping(data.get("metadata")),
        )

    @property
    def passed(self) -> bool:
        return self.correct and not self.failure_reasons

    @property
    def is_refusal(self) -> bool:
        # Import lazily to keep the model contract independent from metric
        # aggregation and to avoid a module import cycle.
        from alpha.memory.evaluation.metrics import is_refusal

        return is_refusal(self.answer)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "answer": self.answer,
            "correct": self.correct,
            "expects_abstention": self.expects_abstention,
            "evidence_ids": list(self.evidence_ids),
            "returned_record_ids": list(self.returned_record_ids),
            "foreign_evidence_ids": list(self.foreign_evidence_ids),
            "superseded_values": list(self.superseded_values),
            "superseded_evidence_ids": list(self.superseded_evidence_ids),
            "update_correct": self.update_correct,
            "composed_tokens": self.composed_tokens,
            "useful_fact_count": self.useful_fact_count,
            "failure_reasons": list(self.failure_reasons),
            "metadata": dict(self.metadata),
        }


@dataclass
class CaseResult:
    """Measured result for one case; unavailable is never a zero or a pass."""

    case_id: str
    ability: Ability
    status: ResultStatus
    answers: dict[str, str] = field(default_factory=dict)
    question_outcomes: list[QuestionOutcome] = field(default_factory=list)
    latency_ms: float = 0.0
    reason: str = ""
    expected_stored_ids: tuple[str, ...] = ()
    stored_ids: tuple[str, ...] = ()
    forgotten_ids: tuple[str, ...] = ()
    write_precision: float | None = None
    write_recall: float | None = None
    extraction_recall: float | None = None
    extraction_relevant_ids: tuple[str, ...] = ()
    extraction_retrieved_ids: tuple[str, ...] = ()
    provider_kind: str = ""
    evidence_kind: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.case_id = str(self.case_id)
        self.ability = normalize_ability(self.ability)
        if self.status not in {"pass", "fail", "unavailable", "error"}:
            raise ValueError(f"invalid case result status: {self.status!r}")
        self.answers = {str(key): str(value) for key, value in dict(self.answers).items()}
        self.question_outcomes = [outcome if isinstance(outcome, QuestionOutcome) else QuestionOutcome.from_dict(outcome) for outcome in self.question_outcomes]
        self.latency_ms = max(0.0, float(self.latency_ms))
        self.reason = str(self.reason)
        self.expected_stored_ids = _string_tuple(self.expected_stored_ids)
        self.stored_ids = _string_tuple(self.stored_ids)
        self.forgotten_ids = _string_tuple(self.forgotten_ids)
        self.extraction_relevant_ids = _string_tuple(self.extraction_relevant_ids)
        self.extraction_retrieved_ids = _string_tuple(self.extraction_retrieved_ids)
        self.provider_kind = str(self.provider_kind)
        self.evidence_kind = str(self.evidence_kind)
        self.metadata = _mapping(self.metadata)

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    @property
    def measured(self) -> bool:
        return self.status in {"pass", "fail"}

    @property
    def outcomes(self) -> list[QuestionOutcome]:
        """Compatibility alias for callers that use ``outcomes`` terminology."""

        return self.question_outcomes

    @property
    def question_results(self) -> list[QuestionOutcome]:
        return self.question_outcomes

    @property
    def evidence(self) -> dict[str, tuple[str, ...]]:
        return {outcome.question_id: outcome.evidence_ids for outcome in self.question_outcomes}

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(record_id for outcome in self.question_outcomes for record_id in outcome.evidence_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "ability": self.ability,
            "status": self.status,
            "answers": dict(self.answers),
            "question_outcomes": [outcome.to_dict() for outcome in self.question_outcomes],
            "latency_ms": self.latency_ms,
            "reason": self.reason,
            "expected_stored_ids": list(self.expected_stored_ids),
            "stored_ids": list(self.stored_ids),
            "forgotten_ids": list(self.forgotten_ids),
            "write_precision": self.write_precision,
            "write_recall": self.write_recall,
            "extraction_recall": self.extraction_recall,
            "extraction_relevant_ids": list(self.extraction_relevant_ids),
            "extraction_retrieved_ids": list(self.extraction_retrieved_ids),
            "provider_kind": self.provider_kind,
            "evidence_kind": self.evidence_kind,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ThresholdCheck:
    """A configured metric gate and its measured value."""

    metric: str
    comparator: Literal["min", "max"]
    threshold: float
    current: float | None
    passed: bool | None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "comparator": self.comparator,
            "threshold": self.threshold,
            "current": self.current,
            "passed": self.passed,
            "reason": self.reason,
        }


@dataclass
class BaselineComparison:
    """Explicit comparison against an injected, read-only baseline file."""

    enabled: bool
    passed: bool
    path: str | None = None
    overall: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_ability: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "passed": self.passed,
            "path": self.path,
            "overall": {key: dict(value) for key, value in self.overall.items()},
            "per_ability": {key: {name: dict(value) for name, value in values.items()} for key, values in self.per_ability.items()},
            "reason": self.reason,
        }


@dataclass
class SuiteReport:
    """Aggregated, machine-readable result of an evaluation run."""

    results: list[CaseResult] = field(default_factory=list)
    per_ability: dict[str, dict[str, int]] = field(default_factory=dict)
    metrics: dict[str, float | None] = field(default_factory=dict)
    ability_metrics: dict[str, dict[str, float | None]] = field(default_factory=dict)
    metric_denominators: dict[str, int] = field(default_factory=dict)
    metric_observed: dict[str, bool] = field(default_factory=dict)
    total_cases: int = 0
    ran_cases: int = 0
    measured_cases: int = 0
    unavailable_cases: int = 0
    error_cases: int = 0
    enabled: bool = True
    provider_kind: str = ""
    evidence_kind: str = ""
    reason: str = ""
    thresholds: list[ThresholdCheck] = field(default_factory=list)
    baseline: BaselineComparison | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.results = list(self.results)
        self.per_ability = {str(key): dict(value) for key, value in self.per_ability.items()}
        for ability in ABILITIES:
            self.per_ability.setdefault(ability, {"total": 0, "pass": 0, "fail": 0, "unavailable": 0, "error": 0})
        self.metrics = {str(key): value for key, value in self.metrics.items()}
        for metric in METRIC_NAMES:
            self.metrics.setdefault(metric, None)
        self.ability_metrics = {str(key): dict(value) for key, value in self.ability_metrics.items()}
        self.metric_denominators = {str(key): int(value) for key, value in self.metric_denominators.items()}
        for metric in self.metrics:
            self.metric_denominators.setdefault(metric, 0)
        self.metric_observed = {str(key): bool(value) for key, value in self.metric_observed.items()}
        for metric in self.metrics:
            self.metric_observed.setdefault(metric, False)
        self.total_cases = max(0, int(self.total_cases))
        self.ran_cases = max(0, int(self.ran_cases))
        self.measured_cases = max(0, int(self.measured_cases))
        self.unavailable_cases = max(0, int(self.unavailable_cases))
        self.error_cases = max(0, int(self.error_cases))
        self.provider_kind = str(self.provider_kind)
        self.evidence_kind = str(self.evidence_kind)
        self.thresholds = list(self.thresholds)
        self.metadata = _mapping(self.metadata)

    @property
    def case_results(self) -> list[CaseResult]:
        return self.results

    @property
    def per_ability_counts(self) -> dict[str, dict[str, int]]:
        return self.per_ability

    @property
    def per_ability_metrics(self) -> dict[str, dict[str, float | None]]:
        return self.ability_metrics

    @property
    def overall_metrics(self) -> dict[str, float | None]:
        return self.metrics

    @property
    def threshold_results(self) -> list[ThresholdCheck]:
        return self.thresholds

    @property
    def passed(self) -> bool:
        """True only when the run has measurements and no failed gate."""

        if not self.enabled or self.measured_cases == 0:
            return False
        if any(result.status != "pass" for result in self.results):
            return False
        if any(check.passed is not True for check in self.thresholds):
            return False
        if self.baseline is not None and self.baseline.enabled and not self.baseline.passed:
            return False
        return True

    @property
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        if not self.results:
            return "empty"
        if any(result.status == "fail" for result in self.results):
            return "fail"
        if any(result.status == "error" for result in self.results):
            return "error"
        if any(check.passed is False for check in self.thresholds) or (self.baseline is not None and self.baseline.enabled and not self.baseline.passed):
            return "fail"
        if any(check.passed is None for check in self.thresholds) or self.unavailable_cases:
            return "unavailable"
        return "pass"

    @property
    def scope(self) -> dict[str, int]:
        return {
            "total_cases": self.total_cases,
            "ran_cases": self.ran_cases,
            "measured_cases": self.measured_cases,
            "unavailable_cases": self.unavailable_cases,
            "error_cases": self.error_cases,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "enabled": self.enabled,
            "status": self.status,
            "passed": self.passed,
            "provider_kind": self.provider_kind,
            "evidence_kind": self.evidence_kind,
            "reason": self.reason,
            "scope": self.scope,
            "total_cases": self.total_cases,
            "ran_cases": self.ran_cases,
            "measured_cases": self.measured_cases,
            "unavailable_cases": self.unavailable_cases,
            "error_cases": self.error_cases,
            "per_ability": {key: dict(value) for key, value in self.per_ability.items()},
            "per_ability_counts": {key: dict(value) for key, value in self.per_ability.items()},
            "metrics": dict(self.metrics),
            "overall_metrics": dict(self.metrics),
            "ability_metrics": {key: dict(value) for key, value in self.ability_metrics.items()},
            "metric_denominators": dict(self.metric_denominators),
            "metric_observed": dict(self.metric_observed),
            "thresholds": [check.to_dict() for check in self.thresholds],
            "baseline": self.baseline.to_dict() if self.baseline is not None else None,
            "results": [result.to_dict() for result in self.results],
            "metadata": dict(self.metadata),
        }


# A small compatibility surface for callers that prefer the explicit names.
MemoryCase = Case
MemoryCaseResult = CaseResult
MemorySuiteReport = SuiteReport

__all__ = [
    "ABILITIES",
    "DEFAULT_NOW",
    "KNOWN_ABILITIES",
    "METRIC_NAMES",
    "Ability",
    "BaselineComparison",
    "Case",
    "CaseResult",
    "Event",
    "MemoryCase",
    "MemoryCaseResult",
    "MemoryRecord",
    "MemorySuiteReport",
    "Question",
    "QuestionOutcome",
    "RecallRecord",
    "Record",
    "ResultStatus",
    "Session",
    "SuiteReport",
    "ThresholdCheck",
    "normalize_ability",
]
