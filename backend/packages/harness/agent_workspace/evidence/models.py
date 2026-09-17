from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

EvidenceKind = Literal["artifact", "benchmark", "trace", "observation"]
CandidateStatus = Literal["proposed", "evaluated", "promoted", "rejected"]
EvaluationVerdict = Literal["promote", "reject", "needs_review"]
MAX_TEXT_LENGTH = 500
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SECRET = re.compile(r"(?i)\b(password|token|api[_-]?key|secret|authorization)\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)")
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;]+")


def validate_text(value: str, field: str, limit: int = 256) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > limit or any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError(f"Invalid {field}")


def sanitize_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid text")
    value = _ANSI.sub("", value)
    value = "".join(" " if unicodedata.category(char).startswith("C") else char for char in value)
    value = _BEARER.sub("Bearer [redacted]", value)
    value = _SECRET.sub(lambda match: f"{match[1]}=[redacted]", value)
    value = value.replace("<", "[").replace(">", "]")
    return " ".join(value.split())[:MAX_TEXT_LENGTH].strip()


def validate_timestamp(value: float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid timestamp")


def text_tuple(value: list[str] | tuple[str, ...], field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"Invalid {field}")
    for item in value:
        validate_text(item, field)
    return tuple(value)


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    owner_id: str
    kind: EvidenceKind
    ref: str
    summary: str
    created_at: float
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_text(self.id, "id")
        validate_text(self.owner_id, "owner_id")
        validate_text(self.ref, "ref", 4096)
        if self.kind not in ("artifact", "benchmark", "trace", "observation"):
            raise ValueError("Invalid kind")
        validate_timestamp(self.created_at)
        object.__setattr__(self, "summary", sanitize_text(self.summary))
        object.__setattr__(self, "tags", text_tuple(self.tags, "tags"))


@dataclass(frozen=True)
class Candidate:
    id: str
    owner_id: str
    title: str
    evidence_ids: tuple[str, ...]
    status: CandidateStatus = "proposed"

    def __post_init__(self) -> None:
        for field in ("id", "owner_id", "title"):
            validate_text(getattr(self, field), field)
        object.__setattr__(self, "evidence_ids", text_tuple(self.evidence_ids, "evidence_ids"))
        if self.status not in ("proposed", "evaluated", "promoted", "rejected"):
            raise ValueError("Invalid status")


@dataclass(frozen=True)
class Evaluation:
    id: str
    candidate_id: str
    score: float
    verdict: EvaluationVerdict
    rationale: str
    evaluator: str
    created_at: float

    def __post_init__(self) -> None:
        for field in ("id", "candidate_id", "evaluator"):
            validate_text(getattr(self, field), field)
        if type(self.score) not in (int, float) or not math.isfinite(self.score) or not 0 <= self.score <= 1:
            raise ValueError("Invalid score")
        object.__setattr__(self, "score", float(self.score))
        if self.verdict not in ("promote", "reject", "needs_review"):
            raise ValueError("Invalid verdict")
        object.__setattr__(self, "rationale", sanitize_text(self.rationale))
        validate_timestamp(self.created_at)


@dataclass(frozen=True)
class Promotion:
    candidate_id: str
    evaluation_id: str
    promoted_at: float

    def __post_init__(self) -> None:
        validate_text(self.candidate_id, "candidate_id")
        validate_text(self.evaluation_id, "evaluation_id")
        validate_timestamp(self.promoted_at)
