"""Deterministic-first scenario classification with an injected model seam."""

from __future__ import annotations

import inspect
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .config import ScenarioConfig
from .models import (
    DEFAULT_SCENARIO,
    ClassificationResult,
    Scenario,
    ScenarioSignals,
    parse_scenario,
)


# Closed statuses are part of the package contract.  Model/library exception
# text never becomes a status; it is kept in the human-readable reason only.
class ClassificationStatus(StrEnum):
    OVERRIDE = "override"
    SIGNALS = "signals"
    INTENT = "intent"
    MODEL = "model"
    BELOW_THRESHOLD = "below_threshold"
    NO_SIGNAL = "no_signal"
    AMBIGUOUS = "ambiguous"
    MODEL_ERROR = "model_error"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    FALLBACK = "fallback"
    DISABLED = "disabled"


class ModelClassificationStatus(StrEnum):
    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    MODEL_ERROR = "model_error"
    INVALID_JSON = "invalid_json"
    INVALID_SHAPE = "invalid_shape"
    UNKNOWN_SCENARIO = "unknown_scenario"
    INVALID_CONFIDENCE = "invalid_confidence"
    UNKNOWN_STATUS = "unknown_status"


CLASSIFICATION_STATUSES: frozenset[str] = frozenset(
    {
        "override",
        "signals",
        "intent",
        "model",
        "below_threshold",
        "no_signal",
        "ambiguous",
        "model_error",
        "invalid_model_output",
        "fallback",
        "disabled",
    }
)
KNOWN_CLASSIFICATION_STATUSES = CLASSIFICATION_STATUSES
MODEL_STATUSES: frozenset[str] = frozenset(
    {
        "ok",
        "not_configured",
        "model_error",
        "invalid_json",
        "invalid_shape",
        "unknown_scenario",
        "invalid_confidence",
        "unknown_status",
    }
)
KNOWN_MODEL_STATUSES = MODEL_STATUSES

_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_FENCE_RE = re.compile(r"^```[A-Za-z0-9_-]*\s*|\s*```$", re.MULTILINE)

_TOOL_PATTERNS: dict[Scenario, tuple[str, ...]] = {
    Scenario.CODING: (
        "code",
        "git",
        "diff",
        "patch",
        "edit_file",
        "write_file",
        "read_file",
        "python",
        "pytest",
        "test",
        "compile",
        "lint",
        "refactor",
        "debug",
        "bash",
        "shell",
    ),
    Scenario.RESEARCH: (
        "web_search",
        "search",
        "browser",
        "browse",
        "fetch",
        "research",
        "arxiv",
        "exa",
        "tavily",
        "firecrawl",
        "source",
    ),
    Scenario.OPERATIONS: (
        "deploy",
        "deployment",
        "rollback",
        "restart",
        "kubectl",
        "kubernetes",
        "docker",
        "terraform",
        "pipeline",
        "monitor",
        "logs",
        "production",
    ),
    Scenario.PLANNING: ("plan", "roadmap", "todo", "schedule", "calendar", "task"),
    Scenario.LEARNING: ("learn", "tutorial", "course", "flashcard", "quiz", "study"),
    Scenario.INCIDENT: ("incident", "alert", "pager", "oncall", "outage", "postmortem"),
}
_FILE_PATTERNS: dict[Scenario, tuple[str, ...]] = {
    Scenario.CODING: (
        "code",
        "source",
        "test",
        "python",
        "javascript",
        "typescript",
        "rust",
        "go",
        "java",
        "diff",
        "patch",
    ),
    Scenario.RESEARCH: ("paper", "article", "pdf", "document", "url", "web", "dataset"),
    Scenario.OPERATIONS: ("log", "metric", "deployment", "manifest", "config", "yaml"),
    Scenario.PLANNING: ("plan", "roadmap", "spec", "requirement", "todo"),
    Scenario.LEARNING: ("note", "course", "tutorial", "flashcard"),
    Scenario.INCIDENT: ("incident", "alert", "postmortem", "trace"),
}
_PROJECT_PATTERNS: dict[Scenario, tuple[str, ...]] = {
    Scenario.RESEARCH: ("research", "paper", "literature"),
    Scenario.OPERATIONS: ("operations", "infra", "production", "sre"),
    Scenario.PLANNING: ("planning", "roadmap", "strategy"),
    Scenario.LEARNING: ("learning", "course", "study"),
    Scenario.INCIDENT: ("incident", "oncall", "outage"),
}
_INTENT_PATTERNS: dict[Scenario, tuple[str, ...]] = {
    Scenario.CODING: (
        "code",
        "coding",
        "bug",
        "debug",
        "implement",
        "refactor",
        "patch",
        "test",
        "compile",
        "fix",
    ),
    Scenario.RESEARCH: (
        "research",
        "investigate",
        "investigation",
        "compare",
        "source",
        "evidence",
        "search",
        "analyze",
    ),
    Scenario.OPERATIONS: (
        "operate",
        "operation",
        "deploy",
        "deployment",
        "release",
        "monitor",
        "infrastructure",
        "production",
        "runbook",
    ),
    Scenario.PLANNING: (
        "plan",
        "planning",
        "roadmap",
        "strategy",
        "design",
        "architect",
        "schedule",
        "organize",
    ),
    Scenario.LEARNING: (
        "learn",
        "learning",
        "study",
        "teach",
        "explain",
        "understand",
        "tutorial",
        "course",
    ),
    Scenario.CONVERSATION: (
        "chat",
        "conversation",
        "talk",
        "discuss",
        "brainstorm",
        "clarify",
        "greeting",
    ),
    Scenario.INCIDENT: (
        "incident",
        "outage",
        "failure",
        "urgent",
        "sev1",
        "production error",
        "emergency",
    ),
}


@dataclass(slots=True)
class ModelParseResult:
    """A bounded parser result; invalid model output never escapes as a label."""

    status: str
    scenario: Scenario = DEFAULT_SCENARIO
    confidence: float = 0.0
    reason: str = ""
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def classification_status_known(status: str) -> bool:
    return str(status) in CLASSIFICATION_STATUSES


def model_status_known(status: str) -> bool:
    return str(status) in MODEL_STATUSES


def _strip_noise(text: str) -> str:
    cleaned = _THINK_RE.sub("", str(text or "")).strip()
    cleaned = _FENCE_RE.sub("", cleaned).strip()
    return cleaned


def _json_object(raw: Any) -> Any | None:
    """Find one JSON object while retaining strict object-shaped validation."""

    if isinstance(raw, Mapping):
        return dict(raw)
    if raw is None:
        return None
    text = _strip_noise(str(raw))
    if not text:
        return None
    candidates = [text]
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        candidates.append(text[index:])
        if isinstance(value, Mapping):
            return dict(value)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, Mapping):
            return dict(value)
    return None


def _response_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "".join(_response_text(item) for item in value)
    if isinstance(value, Mapping):
        content = value.get("content")
        if content is not None:
            return _response_text(content)
        text = value.get("text")
        return _response_text(text) if text is not None else ""
    content = getattr(value, "content", None)
    if content is not None:
        return _response_text(content)
    text = getattr(value, "text", None)
    return _response_text(text) if text is not None else ""


def parse_model_response(value: Any) -> ModelParseResult:
    """Parse strict JSON model output into a closed, honest result."""

    try:
        raw = _response_text(value)
        payload = _json_object(value if isinstance(value, Mapping) else raw)
    except Exception as exc:  # noqa: BLE001 - a hostile adapter object is invalid output
        return ModelParseResult(status="invalid_json", reason=f"could not read model output: {str(exc)[:160]}")
    if payload is None:
        return ModelParseResult(status="invalid_json", raw=raw, reason="model output was not one JSON object")
    supplied_status = payload.get("status")
    if supplied_status is not None:
        normalized_status = str(supplied_status).strip().lower()
        if normalized_status not in MODEL_STATUSES:
            return ModelParseResult(status="unknown_status", raw=raw, reason="model returned an unknown status")
        if normalized_status != "ok":
            return ModelParseResult(status=normalized_status, raw=raw, reason=f"model reported {normalized_status}")
    scenario_value = payload.get("scenario", payload.get("label"))
    if scenario_value is None:
        return ModelParseResult(status="invalid_shape", raw=raw, reason="model JSON lacked a scenario field")
    scenario = parse_scenario(scenario_value, default=None)
    if scenario is None:
        return ModelParseResult(status="unknown_scenario", raw=raw, reason="model returned an unknown scenario")
    confidence_value = payload.get("confidence")
    if confidence_value is None:
        return ModelParseResult(status="invalid_confidence", raw=raw, reason="model JSON lacked a confidence field")
    try:
        confidence = float(confidence_value)
    except (TypeError, ValueError, OverflowError):
        return ModelParseResult(status="invalid_confidence", raw=raw, reason="model confidence was not numeric")
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        return ModelParseResult(status="invalid_confidence", raw=raw, reason="model confidence was outside [0, 1]")
    reason = str(payload.get("reason") or "model classifier selected the scenario").strip()[:500]
    return ModelParseResult(status="ok", scenario=scenario, confidence=confidence, reason=reason, raw=raw)


def _matches(term: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in term for pattern in patterns)


def _strong_signal(signals: ScenarioSignals) -> tuple[Scenario | None, float, str, bool]:
    """Score high-trust runtime observations and report ambiguity honestly."""

    scores: dict[Scenario, float] = {scenario: 0.0 for scenario in Scenario}
    evidence: dict[Scenario, list[str]] = {scenario: [] for scenario in Scenario}
    if signals.is_incident:
        scores[Scenario.INCIDENT] += 5.0
        evidence[Scenario.INCIDENT].append("is_incident")
    if signals.has_error:
        scores[Scenario.INCIDENT] += 4.0
        evidence[Scenario.INCIDENT].append("has_error")
        scores[Scenario.CODING] += 0.25
        evidence[Scenario.CODING].append("has_error context")
    if signals.has_diff:
        scores[Scenario.CODING] += 3.0
        evidence[Scenario.CODING].append("has_diff")

    for tool in signals.tools_used:
        for scenario, patterns in _TOOL_PATTERNS.items():
            if _matches(tool, patterns):
                scores[scenario] += 2.0
                evidence[scenario].append(f"tool:{tool}")
    for file_kind in signals.file_kinds:
        for scenario, patterns in _FILE_PATTERNS.items():
            if _matches(file_kind, patterns):
                scores[scenario] += 1.5
                evidence[scenario].append(f"file:{file_kind}")
    for hint in signals.project_hints:
        for scenario, patterns in _PROJECT_PATTERNS.items():
            if _matches(hint, patterns):
                scores[scenario] += 0.5
                evidence[scenario].append(f"project:{hint}")

    # Reading the field is intentional: time is useful context to a model but
    # is not sufficient evidence to label a work scenario.
    _ = signals.time_of_day
    ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0].value))
    top_scenario, top_score = ranked[0]
    if top_score <= 0.0:
        return None, 0.0, "no tool, file, error, or project signal", False
    tied = [scenario for scenario, score in ranked if score == top_score]
    if len(tied) > 1:
        names = ", ".join(scenario.value for scenario in tied)
        return None, 0.0, f"ambiguous runtime signals ({names})", True
    second_score = ranked[1][1]
    if top_score - second_score < 0.5:
        return None, 0.0, f"ambiguous runtime signals near {top_scenario.value}", True
    confidence = min(0.98, 0.72 + 0.08 * top_score)
    evidence_text = ", ".join(evidence[top_scenario][:3])
    return top_scenario, confidence, f"runtime {evidence_text} signal selected {top_scenario.value}", False


def _has_runtime_signal(signals: ScenarioSignals) -> bool:
    return bool(signals.declared_intent or signals.tools_used or signals.file_kinds or signals.has_error or signals.has_diff or signals.is_incident or signals.project_hints)


def _intent_signal(signals: ScenarioSignals) -> tuple[Scenario | None, float, str, bool]:
    text = (signals.declared_intent or "").strip().lower()
    if not text:
        return None, 0.0, "no declared intent", False
    exact = parse_scenario(text, default=None)
    if exact is not None:
        return exact, 0.82, f"declared intent selected {exact.value}", False
    matches: list[tuple[Scenario, str]] = []
    for scenario, patterns in _INTENT_PATTERNS.items():
        for pattern in patterns:
            if re.search(rf"(?<![a-z]){re.escape(pattern)}(?![a-z])", text):
                matches.append((scenario, pattern))
                break
    unique = {scenario for scenario, _ in matches}
    if not unique:
        return None, 0.0, "declared intent had no recognized scenario keyword", False
    if len(unique) > 1:
        return None, 0.0, "declared intent matched multiple scenarios", True
    scenario = next(iter(unique))
    keyword = next(pattern for candidate, pattern in matches if candidate is scenario)
    return scenario, 0.70, f"declared intent keyword '{keyword}' selected {scenario.value}", False


class ScenarioClassifier:
    """Classify signals with deterministic precedence and an injected model.

    The model is never constructed here.  A caller must pass a model object
    (or a callable) explicitly, which keeps the package hermetic and prevents a
    classifier import from acquiring network credentials.
    """

    def __init__(
        self,
        config: ScenarioConfig | None = None,
        model: Any | None = None,
        *,
        model_classifier: Any | None = None,
    ) -> None:
        self.config = config or ScenarioConfig()
        self.model = model if model is not None else model_classifier

    @property
    def fallback_scenario(self) -> Scenario:
        """The explicit safe fallback configured by the host."""

        return self.config.default_scenario

    def _model_result(self, signals: ScenarioSignals) -> ModelParseResult:
        if not self.config.enable_model_classifier:
            return ModelParseResult(
                status="not_configured",
                reason="model classifier is disabled or no model was injected",
            )
        if self.model is None:
            return ModelParseResult(status="not_configured", reason="model classifier has no injected model")
        prompt = json.dumps(
            {
                "task": "classify the current work scenario",
                "allowed_scenarios": [scenario.value for scenario in Scenario],
                "signals": signals.as_prompt_dict(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        value: Any = None
        try:
            if hasattr(self.model, "classify") and callable(self.model.classify):
                try:
                    value = self.model.classify(signals)
                except TypeError:
                    value = self.model.classify(signals=signals)
            elif callable(self.model):
                try:
                    value = self.model(signals)
                except TypeError:
                    try:
                        value = self.model(signals=signals)
                    except TypeError:
                        value = self.model(prompt)
            elif hasattr(self.model, "invoke") and callable(self.model.invoke):
                try:
                    value = self.model.invoke(prompt, config={"model": self.config.classifier_model})
                except TypeError:
                    try:
                        value = self.model.invoke(prompt)
                    except TypeError:
                        value = self.model.invoke(prompt=prompt)
            elif hasattr(self.model, "complete") and callable(self.model.complete):
                value = self.model.complete(prompt)
            else:
                return ModelParseResult(status="model_error", reason="injected model has no callable seam")
            if inspect.isawaitable(value):
                # The router is synchronous by contract.  Do not hide an
                # un-awaited coroutine behind a fabricated label.
                close = getattr(value, "close", None)
                if callable(close):
                    close()
                return ModelParseResult(status="model_error", reason="injected model returned an awaitable")
        except Exception as exc:  # noqa: BLE001 - model failure is a disclosed fallback
            return ModelParseResult(status="model_error", reason=f"model failure: {str(exc)[:240]}")
        try:
            return parse_model_response(value)
        except Exception as exc:  # noqa: BLE001 - malformed adapter objects are disclosed
            return ModelParseResult(status="model_error", reason=f"model response parse failure: {str(exc)[:200]}")

    def classify(self, signals: ScenarioSignals | Mapping[str, Any] | None) -> ClassificationResult:
        """Classify one signal set; this method never raises for bad model data."""

        fallback = self.fallback_scenario
        fallback_name = fallback.value
        try:
            normalized = signals if isinstance(signals, ScenarioSignals) else ScenarioSignals.model_validate(signals or {})
        except Exception as exc:  # noqa: BLE001 - malformed caller data is a safe fallback
            return ClassificationResult(
                scenario=fallback,
                confidence=0.0,
                status="no_signal",
                reason=f"invalid signals; defaulted to {fallback_name} ({str(exc)[:160]})",
            )

        if normalized.explicit_override:
            override = parse_scenario(normalized.explicit_override, default=None)
            if override is None:
                return ClassificationResult(
                    scenario=fallback,
                    confidence=0.0,
                    status="fallback",
                    reason=f"unknown explicit user override; defaulted to {fallback_name}",
                    source="explicit_override",
                )
            return ClassificationResult(
                scenario=override,
                confidence=1.0,
                status="override",
                reason=f"explicit user override selected {override.value}",
                source="explicit_override",
            )

        scenario, confidence, reason, ambiguous = _strong_signal(normalized)
        if scenario is not None:
            if confidence < self.config.min_confidence:
                return ClassificationResult(
                    scenario=fallback,
                    confidence=confidence,
                    status="below_threshold",
                    reason=f"{reason}; below min_confidence, defaulted to {fallback_name}",
                    source="runtime_signals",
                )
            return ClassificationResult(
                scenario=scenario,
                confidence=confidence,
                status="signals",
                reason=reason,
                source="runtime_signals",
            )
        if ambiguous:
            return ClassificationResult(
                scenario=fallback,
                confidence=0.0,
                status="ambiguous",
                reason=f"{reason}; defaulted to {fallback_name}",
                source="runtime_signals",
            )

        scenario, confidence, reason, ambiguous = _intent_signal(normalized)
        if scenario is not None and not ambiguous:
            if confidence < self.config.min_confidence:
                return ClassificationResult(
                    scenario=fallback,
                    confidence=confidence,
                    status="below_threshold",
                    reason=f"{reason}; below min_confidence, defaulted to {fallback_name}",
                    source="declared_intent",
                )
            return ClassificationResult(
                scenario=scenario,
                confidence=confidence,
                status="intent",
                reason=reason,
                source="declared_intent",
            )
        if ambiguous:
            return ClassificationResult(
                scenario=fallback,
                confidence=0.0,
                status="ambiguous",
                reason=f"{reason}; defaulted to {fallback_name}",
                source="declared_intent",
            )
        if not _has_runtime_signal(normalized):
            return ClassificationResult(
                scenario=fallback,
                confidence=0.0,
                status="no_signal",
                reason=f"no usable runtime signal; defaulted to {fallback_name}",
                source="fallback",
            )

        model_result = self._model_result(normalized)
        if model_result.status == "ok" and model_result.confidence >= self.config.min_confidence:
            return ClassificationResult(
                scenario=model_result.scenario,
                confidence=model_result.confidence,
                status="model",
                reason=f"injected model selected {model_result.scenario.value}: {model_result.reason}",
                source="model",
                model_status=model_result.status,
            )
        if model_result.status == "ok":
            return ClassificationResult(
                scenario=fallback,
                confidence=model_result.confidence,
                status="below_threshold",
                reason=f"model confidence below min_confidence; defaulted to {fallback_name} ({model_result.reason})",
                source="model",
                model_status=model_result.status,
            )
        if model_result.status == "not_configured":
            return ClassificationResult(
                scenario=fallback,
                confidence=0.0,
                status="no_signal",
                reason=f"no usable runtime signal; defaulted to {fallback_name} ({model_result.reason})",
                source="fallback",
                model_status=model_result.status,
            )
        return ClassificationResult(
            scenario=fallback,
            confidence=0.0,
            status="model_error" if model_result.status == "model_error" else "invalid_model_output",
            reason=f"model classifier failed ({model_result.reason}); defaulted to {fallback_name}",
            source="model",
            model_status=model_result.status,
        )


def classify_scenario(
    signals: ScenarioSignals | Mapping[str, Any] | None,
    config: ScenarioConfig | None = None,
    model: Any | None = None,
) -> ClassificationResult:
    """Functional convenience wrapper around :class:`ScenarioClassifier`."""

    return ScenarioClassifier(config=config, model=model).classify(signals)


# Short aliases for callers that naturally use the operation as a verb.
classify = classify_scenario
classify_signals = classify_scenario
parse_classifier_response = parse_model_response


__all__ = [
    "CLASSIFICATION_STATUSES",
    "ClassificationStatus",
    "ModelClassificationStatus",
    "KNOWN_CLASSIFICATION_STATUSES",
    "KNOWN_MODEL_STATUSES",
    "MODEL_STATUSES",
    "ModelParseResult",
    "ScenarioClassifier",
    "classify",
    "classify_scenario",
    "classify_signals",
    "classification_status_known",
    "model_status_known",
    "parse_classifier_response",
    "parse_model_response",
]
