"""Ordered memory-admission hard rules and conservative secret detection.

The seven rule names and their default actions mirror plan section 25.  The
secret detector is intentionally small and testable.  High-confidence shapes
(private keys, provider key prefixes, bearer credentials, and credentialed
connection strings) are certain; entropy-based assignments are disclosed as
uncertain and are still rejected by default so uncertainty fails closed.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .models import AdmissionAction, AdmissionCandidate

RULE_IDS: tuple[str, ...] = (
    "secret_like",
    "explicit_remember",
    "project_decision",
    "user_preference",
    "successful_procedure",
    "transient_status",
    "raw_tool_output",
)
RULE_ACTIONS: dict[str, AdmissionAction] = {
    "secret_like": "reject",
    "explicit_remember": "always_store",
    "project_decision": "durable_project",
    "user_preference": "durable_profile",
    "successful_procedure": "promote_to_skill_candidate",
    "transient_status": "session_only",
    "raw_tool_output": "episodic_archive",
}

_EXPLICIT_RE = re.compile(
    r"^\s*(?:please\s+)?(?:remember|save|retain|keep)\s+"
    r"(?:this|that|these|the\s+following)\b",
    re.IGNORECASE,
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN\s+(?:[A-Z0-9 ]+)?PRIVATE\s+KEY-----",
    re.IGNORECASE,
)
_PROVIDER_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:sk-[A-Za-z0-9_-]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[A-Za-z0-9_-]{30,}"
    r"|xox[baprs]-[A-Za-z0-9-]{16,})"
    r"(?![A-Za-z0-9])"
)
_BEARER_RE = re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{16,})\b", re.IGNORECASE)
_CREDENTIAL_URI_RE = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp)://"
    r"([^/\s:@]+):([^@\s/]+)@",
    re.IGNORECASE,
)
_ENTROPY_ASSIGNMENT_RE = re.compile(
    r"(?im)\b(?:api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|auth[_-]?token|"
    r"password|passwd|pwd)\b\s*[:=]\s*[\"']?"
    r"([A-Za-z0-9+/=_\-.]{20,})[\"']?"
)
_PLACEHOLDER_RE = re.compile(
    r"(?:example|placeholder|redacted|changeme|your[_-]?|not[_-]?a[_-]?secret|test[_-]?only)",
    re.IGNORECASE,
)
# A 12+ character assignment at Shannon entropy >= 3.0 catches both mixed
# alphabets and 32-character hex keys while keeping the result explicitly
# uncertain.  Obvious placeholders are excluded above.
_MIN_ASSIGNMENT_LENGTH = 12
_MIN_ASSIGNMENT_ENTROPY = 3.0


@dataclass(frozen=True, slots=True)
class AdmissionRule:
    """One immutable rule-table entry."""

    rule_id: str
    action: AdmissionAction
    min_confidence: float | None = None
    require_provenance: bool = False
    min_successes: int | None = None

    def __post_init__(self) -> None:
        if self.rule_id not in RULE_ACTIONS:
            raise ValueError(f"unknown admission rule id: {self.rule_id}")
        if self.action != RULE_ACTIONS[self.rule_id]:
            raise ValueError(
                f"rule {self.rule_id!r} must use source-of-truth action {RULE_ACTIONS[self.rule_id]!r}"
            )
        if self.min_confidence is not None and (
            not math.isfinite(self.min_confidence) or not 0.0 <= self.min_confidence <= 1.0
        ):
            raise ValueError("min_confidence must be finite and between 0 and 1")
        if self.min_successes is not None and self.min_successes < 1:
            raise ValueError("min_successes must be at least 1")


DEFAULT_RULES: tuple[AdmissionRule, ...] = (
    AdmissionRule("secret_like", "reject"),
    AdmissionRule("explicit_remember", "always_store"),
    AdmissionRule("project_decision", "durable_project", require_provenance=True),
    AdmissionRule("user_preference", "durable_profile", min_confidence=0.70),
    AdmissionRule("successful_procedure", "promote_to_skill_candidate", min_successes=2),
    AdmissionRule("transient_status", "session_only"),
    AdmissionRule("raw_tool_output", "episodic_archive"),
)


@dataclass(frozen=True, slots=True)
class SecretDetection:
    """Secret detector output, including uncertainty disclosure."""

    is_secret_like: bool
    pattern_id: str
    confidence: str
    reason: str


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    """Internal/public rule result with explicit near-miss disclosure."""

    rule_id: str
    matched: bool
    reason: str = ""
    action: AdmissionAction | None = None
    near_miss: str = ""


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def detect_secret_like(text: str) -> SecretDetection:
    """Detect documented credential shapes without executing or storing text."""

    content = str(text or "")
    if _PRIVATE_KEY_RE.search(content):
        return SecretDetection(True, "private_key_header", "certain", "private key header detected")
    if _PROVIDER_KEY_RE.search(content):
        return SecretDetection(True, "provider_api_key", "certain", "provider API-key prefix detected")
    bearer = _BEARER_RE.search(content)
    if bearer is not None:
        return SecretDetection(True, "bearer_token", "certain", "bearer credential detected")
    if _CREDENTIAL_URI_RE.search(content):
        return SecretDetection(True, "credentialed_connection_string", "certain", "connection string contains credentials")
    entropy_match = _ENTROPY_ASSIGNMENT_RE.search(content)
    if entropy_match is not None:
        value = entropy_match.group(1)
        if (
            len(value) >= _MIN_ASSIGNMENT_LENGTH
            and not _PLACEHOLDER_RE.search(value)
            and _shannon_entropy(value) >= _MIN_ASSIGNMENT_ENTROPY
        ):
            return SecretDetection(
                True,
                "high_entropy_assignment",
                "uncertain",
                "high-entropy credential assignment detected; treated as secret pending verification",
            )
    return SecretDetection(False, "none", "certain", "no documented secret pattern detected")


def _normalized(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _has_tag(candidate: AdmissionCandidate, *names: str) -> bool:
    tags = set(candidate.types)
    subtype = _normalized(candidate.subtype)
    return any(name in tags or name in subtype for name in names)


def _explicit_request(candidate: AdmissionCandidate) -> bool:
    return candidate.explicit_user_request is True or _EXPLICIT_RE.search(candidate.content) is not None


def _nonpromotion_override(candidate: AdmissionCandidate) -> RuleEvaluation | None:
    """Keep uncertain/temporary/volatile claims out of durable promotions."""

    claim_labels = {
        _normalized(candidate.claim_status),
        _normalized(candidate.subtype),
    }
    if claim_labels & {"guess", "uncertain", "uncertain_guess", "hypothesis"}:
        return RuleEvaluation(
            "nonpromotion",
            True,
            "uncertain guess is not eligible for durable profile/project/skill promotion",
            "reject",
        )
    if candidate.is_transient is True:
        return RuleEvaluation(
            "nonpromotion",
            True,
            "transient status cannot be durably promoted",
            "session_only",
        )
    if claim_labels & {"temporary", "temporary_value"}:
        return RuleEvaluation(
            "nonpromotion",
            True,
            "temporary value remains episodic instead of being promoted",
            "episodic_archive",
        )
    if _normalized(candidate.source) in {"tool", "tool_output", "raw_tool_output", "tool_result"}:
        return RuleEvaluation(
            "nonpromotion",
            True,
            "raw tool output cannot be durably promoted",
            "episodic_archive",
        )
    if claim_labels & {"volatile", "volatile_value"}:
        return RuleEvaluation(
            "nonpromotion",
            True,
            "volatile value remains episodic instead of being promoted",
            "episodic_archive",
        )
    third_party = _normalized(candidate.source) in {
        "third_party",
        "external",
        "web",
        "third_party_claim",
    }
    if (claim_labels & {"third_party", "third_party_claim", "unverified_claim"} or third_party) and candidate.is_verified is not True:
        return RuleEvaluation(
            "nonpromotion",
            True,
            "unverified third-party claim is not eligible for durable promotion",
            "episodic_archive",
        )
    return None


def evaluate_rule(
    candidate: AdmissionCandidate,
    rule: AdmissionRule,
    *,
    secret_patterns_enabled: bool = True,
) -> RuleEvaluation:
    """Evaluate one rule without relying on any other rule's side effects."""

    if rule.rule_id == "secret_like":
        if candidate.is_secret_like is True:
            return RuleEvaluation(rule.rule_id, True, "caller marked candidate as secret-like", "reject")
        if not secret_patterns_enabled:
            return RuleEvaluation(rule.rule_id, False, near_miss="secret pattern detection disabled by configuration")
        detection = detect_secret_like(candidate.content)
        if detection.is_secret_like:
            return RuleEvaluation(
                rule.rule_id,
                True,
                f"{detection.reason} ({detection.pattern_id}; confidence={detection.confidence})",
                "reject",
            )
        return RuleEvaluation(rule.rule_id, False, near_miss=detection.reason)

    if rule.rule_id == "explicit_remember":
        if _explicit_request(candidate):
            return RuleEvaluation(rule.rule_id, True, "explicit user request to remember the candidate")
        return RuleEvaluation(rule.rule_id, False, near_miss="no explicit remember request")

    if rule.rule_id == "project_decision":
        is_project = _has_tag(candidate, "project_decision", "project_decisions") or (
            "decision" in candidate.types and bool(candidate.project_id)
        )
        if not is_project:
            return RuleEvaluation(rule.rule_id, False, near_miss="candidate is not a project decision")
        if rule.require_provenance and not candidate.provenance.strip():
            return RuleEvaluation(rule.rule_id, True, "durable project decision requires provenance", "reject")
        guarded = _nonpromotion_override(candidate)
        if guarded is not None:
            return RuleEvaluation(rule.rule_id, True, guarded.reason, guarded.action)
        return RuleEvaluation(rule.rule_id, True, "durable project decision has provenance")

    if rule.rule_id == "user_preference":
        if not _has_tag(candidate, "user_preference", "preference", "stable_preference"):
            return RuleEvaluation(rule.rule_id, False, near_miss="candidate is not a user preference")
        if candidate.confidence is None or rule.min_confidence is None or candidate.confidence < rule.min_confidence:
            observed = "missing" if candidate.confidence is None else f"{candidate.confidence:.6g}"
            return RuleEvaluation(
                rule.rule_id,
                False,
                near_miss=(
                    f"user preference confidence {observed} does not meet "
                    f"min_confidence={rule.min_confidence}"
                ),
            )
        guarded = _nonpromotion_override(candidate)
        if guarded is not None:
            return RuleEvaluation(rule.rule_id, True, guarded.reason, guarded.action)
        return RuleEvaluation(rule.rule_id, True, "stable user preference meets min_confidence")

    if rule.rule_id == "successful_procedure":
        if not _has_tag(candidate, "successful_procedure", "procedure", "skill"):
            return RuleEvaluation(rule.rule_id, False, near_miss="candidate is not a procedure")
        if candidate.success_count is None or rule.min_successes is None or candidate.success_count < rule.min_successes:
            observed = "missing" if candidate.success_count is None else str(candidate.success_count)
            return RuleEvaluation(
                rule.rule_id,
                False,
                near_miss=(
                    f"procedure successes {observed} do not meet "
                    f"min_successes={rule.min_successes}"
                ),
            )
        guarded = _nonpromotion_override(candidate)
        if guarded is not None:
            return RuleEvaluation(rule.rule_id, True, guarded.reason, guarded.action)
        return RuleEvaluation(rule.rule_id, True, "successful procedure meets min_successes")

    if rule.rule_id == "transient_status":
        if candidate.is_transient is True:
            return RuleEvaluation(rule.rule_id, True, "candidate is transient status")
        return RuleEvaluation(rule.rule_id, False, near_miss="candidate is not transient")

    if rule.rule_id == "raw_tool_output":
        if _normalized(candidate.source) in {"tool", "tool_output", "raw_tool_output", "tool_result"} or _has_tag(candidate, "raw_tool_output"):
            return RuleEvaluation(rule.rule_id, True, "candidate is raw tool output")
        return RuleEvaluation(rule.rule_id, False, near_miss="candidate is not raw tool output")

    raise ValueError(f"unknown admission rule id: {rule.rule_id}")


__all__ = [
    "DEFAULT_RULES",
    "RULE_ACTIONS",
    "RULE_IDS",
    "AdmissionRule",
    "RuleEvaluation",
    "SecretDetection",
    "detect_secret_like",
    "evaluate_rule",
]
