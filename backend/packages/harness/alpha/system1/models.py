"""Typed System 1 decision schemas: request/response pairs for the three
non-autoregressive primitives (choice, score, noul).

Every result carries its provenance (``engine`` + ``fallback_reason``) so each
decision the harness returns is attributable: a fallback path is always
disclosed on the result object, never silently substituted, and a result is
never fabricated when both the cloud and local paths fail (the exception
propagates instead).
"""

from __future__ import annotations

import math
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

# Provenance labels shared by classifier and engine (single source of truth).
ENGINE_JEV = "jev_cloud"
ENGINE_LOCAL = "local_reflex"


class DecisionType(StrEnum):
    """The three typed decision primitives of the System 1 harness."""

    CHOICE = "choice"
    SCORE = "score"
    NOUL = "noul"


class _DecisionMeta(BaseModel):
    """Provenance attached to every decision."""

    engine: str = Field(min_length=1, description="Engine that produced this decision: 'jev_cloud' or 'local_reflex'.")
    fallback_reason: str | None = Field(
        default=None,
        description=(
            "Disclosed when the primary (cloud) path was unavailable, was skipped to avoid blocking, or violated its response contract, and this decision therefore came from the local fallback. None on a clean primary-path decision."
        ),
    )


class ChoiceRequest(BaseModel):
    """Select exactly one winner from an unordered candidate set."""

    context: str = Field(min_length=1, description="Situational context the decision is made under.")
    candidates: list[str] = Field(
        min_length=1,
        description="Unordered candidate set; the winner is strictly drawn from this set (no generation).",
    )

    @model_validator(mode="after")
    def _check_candidates(self) -> ChoiceRequest:
        if not self.context.strip():
            raise ValueError("context must be a non-empty string")
        for cand in self.candidates:
            if not isinstance(cand, str) or not cand.strip():
                raise ValueError("every candidate must be a non-empty string")
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError("candidates must be unique: a probability distribution cannot address duplicates")
        return self


class ChoiceResult(_DecisionMeta):
    """Winner plus the full probability distribution over the candidate set."""

    winner: str = Field(description="The selected candidate; always a member of the request's candidate set.")
    probabilities: dict[str, float] = Field(description="Distribution over exactly the candidate set; sums to 1.0.")

    @model_validator(mode="after")
    def _check_distribution(self) -> ChoiceResult:
        if not self.probabilities:
            raise ValueError("probabilities must be a non-empty distribution")
        for name, prob in self.probabilities.items():
            if not math.isfinite(prob) or prob < 0.0 or prob > 1.0:
                raise ValueError(f"probability for {name!r} must be a finite value in [0, 1], got {prob!r}")
        if self.winner not in self.probabilities:
            raise ValueError(f"winner {self.winner!r} is not present in the probability distribution")
        total = sum(self.probabilities.values())
        if abs(total - 1.0) > 0.05:
            raise ValueError(f"probabilities must sum to 1.0 (got {total:.4f})")
        if self.probabilities[self.winner] < max(self.probabilities.values()) - 1e-6:
            raise ValueError("the declared winner must carry the highest probability in the distribution")
        return self


class ScoreRequest(BaseModel):
    """Assign a continuous value on an ordered scale (default 0.0-1.0)."""

    context: str = Field(min_length=1, description="The thing being scored (e.g. a command, path, or URL).")
    question: str = Field(default="", description="Optional framing question; provenance only for local evidence scoring.")
    scale: tuple[float, float] = Field(default=(0.0, 1.0), description="Ordered (low, high) bounds of the scale.")

    @model_validator(mode="after")
    def _check_request(self) -> ScoreRequest:
        if not self.context.strip():
            raise ValueError("context must be a non-empty string")
        lo, hi = self.scale
        if not (math.isfinite(lo) and math.isfinite(hi)) or lo >= hi:
            raise ValueError(f"scale must be finite with lo < hi, got {self.scale!r}")
        return self


class ScoreResult(_DecisionMeta):
    """Ordered continuous score plus a confidence in [0, 1]."""

    score: float = Field(description="Value on ``scale``; ordered (higher means more of the scored axis, e.g. safer).")
    confidence: float = Field(description="How much evidence backs this score, in [0, 1]; low = weak/no signal.")
    scale: tuple[float, float] = Field(default=(0.0, 1.0), description="The scale the score is expressed on.")

    @model_validator(mode="after")
    def _check_result(self) -> ScoreResult:
        lo, hi = self.scale
        if not (math.isfinite(lo) and math.isfinite(hi)) or lo >= hi:
            raise ValueError(f"scale must be finite with lo < hi, got {self.scale!r}")
        if not math.isfinite(self.score) or not lo <= self.score <= hi:
            raise ValueError(f"score {self.score!r} must be finite and within scale {self.scale!r}")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be a finite value in [0, 1], got {self.confidence!r}")
        return self


class NoulRequest(BaseModel):
    """Strict binary yes/no gate over a context, framed by a question."""

    context: str = Field(min_length=1, description="Evidence the gate decides on; the question alone can never decide it.")
    question: str = Field(min_length=1, description="The binary question, e.g. 'Has the objective been satisfied?'")
    threshold: float = Field(default=0.5, description="Decision threshold on P(yes), strictly inside (0, 1).")

    @model_validator(mode="after")
    def _check_request(self) -> NoulRequest:
        if not self.context.strip():
            raise ValueError("context must be a non-empty string")
        if not self.question.strip():
            raise ValueError("question must be a non-empty string")
        if not math.isfinite(self.threshold) or not 0.0 < self.threshold < 1.0:
            raise ValueError(f"threshold must be strictly inside (0, 1), got {self.threshold!r}")
        return self


class NoulResult(_DecisionMeta):
    """Strict binary decision plus the probability of THAT decision."""

    decision: bool = Field(description="The yes/no outcome (strict boolean, never coerced from strings).")
    probability: float = Field(description="P(decision) in [0, 1]; low values mean honest uncertainty.")
    threshold: float = Field(default=0.5, description="Threshold the decision was applied against.")

    @field_validator("decision", mode="before")
    @classmethod
    def _strict_boolean(cls, value: object) -> object:
        # Fail closed on contract violations: only a real JSON boolean is accepted.
        if not isinstance(value, bool):
            raise ValueError(f"decision must be a strict boolean, got {type(value).__name__}")
        return value

    @model_validator(mode="after")
    def _check_result(self) -> NoulResult:
        if not math.isfinite(self.probability) or not 0.0 <= self.probability <= 1.0:
            raise ValueError(f"probability must be a finite value in [0, 1], got {self.probability!r}")
        if not math.isfinite(self.threshold) or not 0.0 < self.threshold < 1.0:
            raise ValueError(f"threshold must be strictly inside (0, 1), got {self.threshold!r}")
        return self
