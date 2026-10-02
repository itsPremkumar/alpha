"""Staged experience sanitisation (prompt §6).

The pipeline
------------
::

    raw experience
        -> secret detection      (delegates to alpha.learning.experience.store)
        -> sensitive-data filter (delegates to the same screen)
        -> prompt-injection detection
        -> provenance validation
        -> quality validation
        -> deduplication
        -> experience candidate

Why this is a pipeline and not another regex
--------------------------------------------
Alpha already screens experience content for credential/PII shapes, in
:data:`alpha.learning.experience.store.SECRET_PATTERNS`. That screen is
**reused, not reimplemented** — :func:`alpha.learning.experience.store.find_secret_shape`
is called directly, so there is exactly one secret denylist in the codebase and a
pattern added there protects this path too. What the screen does not do is
refuse *untrusted instruction-shaped content*, validate provenance, reject a
record with nothing to learn from, or deduplicate. Those four are this module's
job.

Refusal is always total
-----------------------
A stage either passes the record through untouched or refuses the whole record
with a stage name and reason. There is no "sanitise the secret out and keep the
rest" path, because redacting a credential-shaped token out of a lesson produces
a lesson that teaches something slightly wrong, and storing it as though it were
clean is worse than refusing it.

Every refusal is reportable
---------------------------
:class:`SanitizerReport` carries the per-stage results, so a caller can answer
"was this refused, and why?" without re-running the pipeline. A stage that was
skipped is reported as ``skipped`` with a reason, never as ``passed``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.learning.experience.models import ExperienceRecord
from alpha.learning.experience.store import SECRET_PATTERNS, find_secret_shape


def _first_metadata_hit(
    metadata: Any,
    denylist: tuple[tuple[re.Pattern[str], str], ...],
    *,
    max_nodes: int = 500,
    budget: list[int] | None = None,
) -> tuple[str, str] | None:
    """Depth-first walk of ``metadata`` returning ``(path, label)`` at the first hit.

    Every **leaf value** is screened, not the serialised document, because the
    shared denylist's assignment patterns are defeated by JSON quoting (see
    :meth:`ExperienceSanitizer.check_sensitive`). Keys are screened too: a
    credential pasted into a key name is still a credential.

    The walk is bounded by ``max_nodes`` and by string length. A metadata dict
    that hits the bound does not silently pass — the caller receives
    ``("...", "metadata walk truncated")``-shaped disclosure via the budget, so
    "we screened everything" is never claimed for a tree we did not finish.
    """
    counter = budget if budget is not None else [max_nodes]
    if counter[0] <= 0:
        return None
    counter[0] -= 1
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            counter[0] -= 1
            if counter[0] <= 0:
                return None
            label = find_secret_shape(str(key))
            if label:
                return (str(key), label)
            # Screen the key/value PAIR as well as the bare value. The denylist's
            # assignment patterns are written against `password=hunter2`, so a
            # value screened on its own ("hunter2hunter2") matches nothing while
            # the pair matches the rule. Reconstructing the assignment here is
            #: what makes the JSON-quoted shape screenable without forking the
            #: rule set.
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                pair_label = find_secret_shape(f"{key}={value}") or find_secret_shape(f"{key}: {value}")
                if pair_label:
                    return (str(key), pair_label)
            hit = _first_metadata_hit(value, denylist, max_nodes=max_nodes, budget=counter)
            if hit is not None:
                child_path, child_label = hit
                return (f"{key}.{child_path}", child_label)
        return None
    if isinstance(metadata, (list, tuple)):
        for index, value in enumerate(metadata):
            hit = _first_metadata_hit(value, denylist, max_nodes=max_nodes, budget=counter)
            if hit is not None:
                child_path, child_label = hit
                return (f"[{index}].{child_path}", child_label)
        return None
    text = str(metadata)
    for pattern, label in denylist:
        if pattern.search(text):
            return (label, label)
    return None


def prose_text(record: ExperienceRecord) -> str:
    """The human-readable prose of a record, **excluding** ``metadata``.

    :func:`alpha.learning.experience.store.record_text` is the bank's own
    screen input and includes ``metadata``. The sanitiser needs a prose-only
    view so its prose stage and its metadata stage are distinct and both
    reachable; see :meth:`ExperienceSanitizer.check_secrets`.
    """
    return "\n".join(
        [
            record.task_goal,
            record.statement,
            *record.lessons_learned,
            *record.pitfalls_to_avoid,
            *record.tags,
            *record.error_types,
            *record.modified_files,
        ]
    )


__all__ = [
    "Stage",
    "Outcome",
    "StageResult",
    "SanitizerReport",
    "ExperienceSanitizer",
    "default_sanitizer",
    "prose_text",
    "MIN_PROVENANCE_FIELDS",
    "MIN_QUALITY_CHARS",
]

#: Provenance fields an experience candidate must carry to be learnable. This is
#: prompt §6's provenance requirement expressed as a minimum, not a schema:
#: :class:`~alpha.intelligence.models.ExperienceTelemetry` carries the full set
#: (proposed/executed/verified/criticized/human_confirmed) and any subset
#: satisfying this minimum is accepted.
MIN_PROVENANCE_FIELDS: tuple[str, ...] = ("source_task", "created_by")

#: Shortest acceptable textual content. Below this an experience cannot carry a
#: lesson, and storing it only adds noise to the reservoir.
MIN_QUALITY_CHARS = 12


class Stage(StrEnum):
    """Pipeline stages, in execution order."""

    SECRET_DETECTION = "secret_detection"
    SENSITIVE_FILTER = "sensitive_filter"
    INJECTION_DETECTION = "injection_detection"
    PROVENANCE_VALIDATION = "provenance_validation"
    QUALITY_VALIDATION = "quality_validation"
    DEDUPLICATION = "deduplication"


class Outcome(StrEnum):
    PASSED = "passed"
    REFUSED = "refused"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class StageResult:
    """One stage's verdict."""

    stage: Stage
    outcome: Outcome
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage.value, "outcome": self.outcome.value, "reason": self.reason, "detail": dict(self.detail)}


@dataclass
class SanitizerReport:
    """The full pipeline result. ``accepted`` is the single answer."""

    accepted: bool
    stages: list[StageResult] = field(default_factory=list)
    refused_at: Stage | None = None
    reason: str = ""
    duplicate_of: str = ""
    """When deduplication refused, the experience id this one duplicates."""

    def stage(self, stage: Stage) -> StageResult | None:
        for result in self.stages:
            if result.stage is stage:
                return result
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "refused_at": self.refused_at.value if self.refused_at else None,
            "reason": self.reason,
            "duplicate_of": self.duplicate_of,
            "stages": [result.to_dict() for result in self.stages],
        }


#: Prompt-injection shapes. An experience is *data about* past work; text that
#: issues instructions to a future reader is an attack surface on the replay
#: path, because a replayed experience is, by construction, fed back to a model.
#: The list is deliberately narrow — it targets imperative instruction phrasing
#: aimed at an agent, not ordinary prose that happens to contain a verb.
_INJECTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bignore (?:all |any )?(?:previous|prior|above|earlier) instructions?\b"), "instruction-override phrasing"),
    (re.compile(r"(?i)\bdisregard (?:all |any )?(?:previous|prior|above|earlier|system)\b"), "instruction-override phrasing"),
    (re.compile(r"(?i)\byou are now\b.{0,40}\b(?:mode|assistant|agent|dan)\b"), "role-reassignment phrasing"),
    (re.compile(r"(?i)\bnew (?:system )?(?:instructions?|prompt)s?:\s"), "inline instruction injection"),
    (re.compile(r"(?i)\b(?:reveal|print|show|output|repeat)\b.{0,30}\b(?:your |the )?(?:system prompt|api key|secret|credentials?)\b"), "credential-exfiltration instruction"),
    (re.compile(r"(?i)<\s*(?:system|assistant|important)\s*>"), "chat-role delimiter injected into stored content"),
    (re.compile(r"(?i)\[\s*(?:INST|/INST|SYSTEM)\s*\]"), "instruction-tuning delimiter"),
    (re.compile(r"(?i)\bexfiltrat\w+\b"), "exfiltration intent"),
)


class ExperienceSanitizer:
    """The staged sanitiser.

    Args:
        seen_digests: Optional mapping of content digest -> experience id,
            supplied by the caller to make deduplication work across processes
            and restarts. When ``None``, the deduplication stage is reported
            ``skipped`` rather than silently passing.
        min_quality_chars: Override for :data:`MIN_QUALITY_CHARS`.
    """

    def __init__(
        self,
        *,
        seen_digests: Mapping[str, str] | None = None,
        min_quality_chars: int = MIN_QUALITY_CHARS,
        injection_patterns: tuple[tuple[re.Pattern[str], str], ...] | None = None,
        max_metadata_nodes: int = 500,
    ) -> None:
        self.seen_digests = dict(seen_digests) if seen_digests is not None else None
        if min_quality_chars < 0:
            raise ValueError(f"min_quality_chars must be >= 0, got {min_quality_chars}")
        self.min_quality_chars = int(min_quality_chars)
        self.injection_patterns = tuple(injection_patterns) if injection_patterns is not None else _INJECTION_PATTERNS
        # Bound the metadata walk so a large or deeply nested metadata dict
        #: cannot make sanitisation an unbounded cost on the write path.
        self.max_metadata_nodes = max_metadata_nodes
        self._denylist = SECRET_PATTERNS

    # -- individual stages --------------------------------------------------

    def check_secrets(self, record: ExperienceRecord) -> StageResult:
        """Delegate to the existing secret/PII screen, over the **prose** fields.

        Scope matters here. :func:`alpha.learning.experience.store.record_text`
        — the helper the bank itself screens with — concatenates the prose fields
        *and* ``metadata``. Reusing it verbatim would make the metadata check
        below unreachable, because a credential in ``metadata`` would be caught
        at this stage first and the pipeline would never report which surface it
        came from. So this stage scans prose only, and
        :meth:`check_sensitive` scans the metadata with the same denylist: one
        rule set, two surfaces.
        """
        label = find_secret_shape(prose_text(record))
        if label:
            return StageResult(
                stage=Stage.SECRET_DETECTION,
                outcome=Outcome.REFUSED,
                reason=f"content matches a secret/PII denylist pattern ({label}); an experience bank must not store credentials or personal data",
                detail={"surface": "prose"},
            )
        return StageResult(stage=Stage.SECRET_DETECTION, outcome=Outcome.PASSED, detail={"surface": "prose"})

    def check_sensitive(self, record: ExperienceRecord) -> StageResult:
        """Second pass over the **metadata** surface, with the same denylist.

        A credential can hide in ``metadata`` where no prose field carried it.
        Reporting it as its own stage means an operator can see *which* surface
        leaked rather than only that something did.

        Metadata is walked **value-first**, and that detail is load-bearing. The
        shared denylist's credential-assignment patterns match ``password=hunter2``
        but not JSON's ``"password": "hunter2"`` — the closing quote before the
        colon defeats ``\\s*[:=]``. Screening the serialised document alone would
        therefore let the most common metadata shape through this stage entirely.
        Checking every nested *value* against the same denylist closes that while
        keeping exactly one rule set in the codebase.
        """
        hit = _first_metadata_hit(record.metadata, self._denylist)
        if hit is not None:
            path, label = hit
            return StageResult(
                stage=Stage.SENSITIVE_FILTER,
                outcome=Outcome.REFUSED,
                reason=f"record metadata matches a secret/PII denylist pattern ({label}) at metadata.{path}",
                detail={"surface": "metadata", "path": path},
            )
        return StageResult(stage=Stage.SENSITIVE_FILTER, outcome=Outcome.PASSED, detail={"surface": "metadata"})

    def check_injection(self, record: ExperienceRecord) -> StageResult:
        """Refuse instruction-shaped content aimed at a future reader."""
        haystack = prose_text(record)
        for pattern, label in self.injection_patterns:
            if pattern.search(haystack):
                return StageResult(
                    stage=Stage.INJECTION_DETECTION,
                    outcome=Outcome.REFUSED,
                    reason=(f"record content matches a prompt-injection pattern ({label}); an experience is replayed back to a model, so stored instruction-shaped text is an attack surface on the replay path"),
                    detail={"surface": "prose"},
                )
        return StageResult(stage=Stage.INJECTION_DETECTION, outcome=Outcome.PASSED)

    def check_provenance(self, record: ExperienceRecord) -> StageResult:
        """Require enough provenance to attribute the experience.

        Two acceptable routes, and the choice is reported:

        * the bank rule already in force — a non-empty ``evidence`` list; or
        * the telemetry roles (:mod:`alpha.intelligence.models.ExperienceTelemetry`)
          when the caller attached them.

        Accepting either keeps backward compatibility with the existing
        ``ExperienceStore.add`` contract while still refusing a record with no
        attribution at all.
        """
        if any(str(ref).strip() for ref in record.evidence):
            return StageResult(
                stage=Stage.PROVENANCE_VALIDATION,
                outcome=Outcome.PASSED,
                detail={"route": "evidence", "evidence_count": len(record.evidence)},
            )
        source_task = record.metadata.get("source_task")
        created_by = record.metadata.get("created_by")
        have = [name for name, value in (("source_task", source_task), ("created_by", created_by)) if value]
        if len(have) >= len(MIN_PROVENANCE_FIELDS):
            return StageResult(
                stage=Stage.PROVENANCE_VALIDATION,
                outcome=Outcome.PASSED,
                detail={"route": "metadata", "fields": have},
            )
        missing = [name for name in MIN_PROVENANCE_FIELDS if not (source_task if name == "source_task" else created_by)]
        return StageResult(
            stage=Stage.PROVENANCE_VALIDATION,
            outcome=Outcome.REFUSED,
            reason=(f"no provenance: an experience must cite a non-empty evidence list, or carry {' and '.join(MIN_PROVENANCE_FIELDS)} in metadata (missing: {', '.join(missing)})"),
            detail={"missing": missing},
        )

    def check_quality(self, record: ExperienceRecord) -> StageResult:
        """Refuse a record with nothing to learn from."""
        content = "\n".join([record.statement, *record.lessons_learned, *record.pitfalls_to_avoid, record.task_goal]).strip()
        if len(content) < self.min_quality_chars:
            return StageResult(
                stage=Stage.QUALITY_VALIDATION,
                outcome=Outcome.REFUSED,
                reason=(f"quality: the record carries {len(content)} character(s) of learnable content, below the minimum {self.min_quality_chars}; storing it only adds noise to the replay reservoir"),
                detail={"content_chars": len(content), "minimum": self.min_quality_chars},
            )
        if not any((record.lessons_learned, record.pitfalls_to_avoid, record.statement)):
            return StageResult(
                stage=Stage.QUALITY_VALIDATION,
                outcome=Outcome.REFUSED,
                reason="quality: the record has content but no lesson, pitfall, or statement to replay",
            )
        return StageResult(stage=Stage.QUALITY_VALIDATION, outcome=Outcome.PASSED, detail={"content_chars": len(content)})

    def check_duplicate(self, record: ExperienceRecord, digest: str) -> StageResult:
        """Refuse a content duplicate against the caller-supplied digest index."""
        if self.seen_digests is None:
            return StageResult(
                stage=Stage.DEDUPLICATION,
                outcome=Outcome.SKIPPED,
                reason="no digest index was supplied, so deduplication could not run",
            )
        existing = self.seen_digests.get(digest)
        if existing:
            return StageResult(
                stage=Stage.DEDUPLICATION,
                outcome=Outcome.REFUSED,
                reason=f"duplicate content: this experience repeats {existing}",
                detail={"digest": digest, "existing_experience_id": existing},
            )
        return StageResult(stage=Stage.DEDUPLICATION, outcome=Outcome.PASSED, detail={"digest": digest})

    # -- pipeline -----------------------------------------------------------

    def sanitize(self, record: ExperienceRecord, *, digest: str = "") -> SanitizerReport:
        """Run every stage in order, stopping at the first refusal."""
        if not isinstance(record, ExperienceRecord):
            raise TypeError(f"sanitize expects an ExperienceRecord, got {type(record).__name__}")
        stages: list[StageResult] = []
        pipeline = (
            (Stage.SECRET_DETECTION, lambda: self.check_secrets(record)),
            (Stage.SENSITIVE_FILTER, lambda: self.check_sensitive(record)),
            (Stage.INJECTION_DETECTION, lambda: self.check_injection(record)),
            (Stage.PROVENANCE_VALIDATION, lambda: self.check_provenance(record)),
            (Stage.QUALITY_VALIDATION, lambda: self.check_quality(record)),
            (Stage.DEDUPLICATION, lambda: self.check_duplicate(record, digest or _content_digest(record))),
        )
        duplicate_of = ""
        for stage, run in pipeline:
            result = run()
            stages.append(result)
            if result.outcome is Outcome.REFUSED:
                if stage is Stage.DEDUPLICATION:
                    duplicate_of = str(result.detail.get("existing_experience_id", ""))
                return SanitizerReport(
                    accepted=False,
                    stages=stages,
                    refused_at=stage,
                    reason=result.reason,
                    duplicate_of=duplicate_of,
                )
        # A skipped stage is not a pass: it is surfaced so the caller can see
        # dedup did not actually run rather than assuming it did.
        return SanitizerReport(accepted=True, stages=stages)


def _content_digest(record: ExperienceRecord) -> str:
    import hashlib
    import json

    payload = json.dumps(
        {
            "goal": record.task_goal,
            "statement": record.statement,
            "lessons": sorted(record.lessons_learned),
            "pitfalls": sorted(record.pitfalls_to_avoid),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def default_sanitizer(seen_digests: Mapping[str, str] | None = None) -> ExperienceSanitizer:
    """The shipped sanitiser."""
    return ExperienceSanitizer(seen_digests=seen_digests)
