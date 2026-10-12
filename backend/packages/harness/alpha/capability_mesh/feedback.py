"""Specialist-generated checks and the user correction/preference loop.

Plan §20 (Feature S) asks the system to write the test that would reveal an
answer is wrong — and says, in one line, that "all generated tests must be
treated as untrusted until reviewed and executed". Plan §21 (Feature T) asks for
a lightweight way for a user to correct the system, and says each item is "a
categorized observation, not universal truth". This module is both ideas as
records and pure functions, and the honesty rules are the point:

* **A self-generated check is a proposal.** :func:`review_check` refuses to
  accept a **non-deterministic** check at all — a check that does not give the
  same answer twice cannot be evidence, and letting it in would let a model
  weaken a suite by shipping a flaky test. :class:`CheckProvenance` refuses to be
  hand-built into claiming otherwise. Acceptance here records a REVIEW, never a
  pass: the check still has to actually run (plan §20's "reviewed AND executed").
* **Private task content is never stored.** A :class:`UserFeedback` carries an
  opaque ``task_fingerprint`` and nothing else task-shaped — no prompt, no
  transcript, no path. The fingerprint is length-bounded because an opaque id is
  short; a paragraph here means someone stored task content in a field whose
  whole purpose is not storing it.
* **A handful of ratings never reorders routing.** Below
  :data:`FEEDBACK_MIN_SAMPLES` the preference weight is ``0.0`` with the reason
  "insufficient feedback" — plan §21 asks for undo/ignore and for preference
  memory to stay separate from objective capability measurement, so a preference
  signal that fires off three clicks is not a measurement.
* **An unreadable ledger is disclosed, never reset.** :class:`FeedbackLedger`
  owns one ``feedback.json`` written atomically (temp + fsync +
  ``os.replace``, UTF-8); a corrupt document raises :class:`FeedbackStoreError`
  naming the cause rather than starting empty and later writing amnesia over the
  file. A *missing* file is genuinely empty — absence is not corruption.

Durable state is single-process JSON: atomic against a crash in one process,
**never cross-process exactly-once**. No lock is taken here, and a second
Gateway worker sharing the directory must serialize writes itself.

Nothing here runs a check, calls a model, or changes a routing decision: this is
the record shape plus the review gate, and there is still no tool, router or
executor wiring.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

#: The single JSON document a :class:`FeedbackLedger` owns.
FEEDBACK_FILE_NAME = "feedback.json"

#: Bumped when the on-disk shape changes; a document carrying a different
#: version is refused rather than guessed at.
FEEDBACK_STORE_VERSION = 1

#: The minimum number of ratings before a preference weight may be anything but
#: ``0.0``. A documented starting policy (plan §21), to be tuned against measured
#: routing outcomes.
FEEDBACK_MIN_SAMPLES = 5

#: A generated check's description is a sentence, not an essay.
MAX_DESCRIPTION_CHARS = 400

#: A user's correction is bounded so feedback cannot become a task-content
#: smuggling channel.
MAX_CORRECTION_CHARS = 2000

#: An opaque fingerprint is short. ``fingerprint()`` in ``memory.py`` produces a
#: 19-character ``fp_`` + 16 hex string; this bound is several times that, so a
#: paragraph of prompt text is refused rather than stored.
MAX_FINGERPRINT_CHARS = 128

#: Clock-skew tolerance on ``recorded_at``. Records are written with whole
#: seconds, so a stamp a moment ahead of the reading clock is not treated as
#: forgery; anything further ahead than this is refused.
_FUTURE_TOLERANCE = timedelta(seconds=5)


class CheckKind(StrEnum):
    """What kind of check a specialist proposed (plan §20)."""

    UNIT = "unit"
    MUTATION = "mutation"
    PROPERTY = "property"
    SOURCE_BACKED = "source_backed"


def _non_empty(value: Any, field_name: str) -> str:
    """Coerce to ``str`` and refuse empty, naming the field."""
    text = str(value)
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _bounded_text(value: Any, field_name: str, limit: int) -> str:
    """A non-empty string of at most ``limit`` characters — refused, never truncated."""
    text = str(value)
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    if len(text) > limit:
        raise ValueError(f"{field_name} must be at most {limit} characters, got {len(text)}: bounds are refused, never silently truncated")
    return text


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    """Parse an ISO-8601 stamp into an aware UTC datetime, refusing the future."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty ISO-8601 string, got {value!r}")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp, got {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    now = datetime.now(UTC)
    if parsed > now + _FUTURE_TOLERANCE:
        raise ValueError(f"{field_name} must not be in the future: {value!r} is ahead of the clock ({now.isoformat()})")
    return parsed


# ----------------------------------------------------------- generated check


@dataclass(frozen=True)
class GeneratedCheck:
    """One specialist-proposed check: what it asserts and where it points.

    Plan §20 asks for requirements-to-test traceability and independent test
    authoring, so ``target`` and ``source_agent`` are both REQUIRED non-empty: a
    check nobody can attribute to a file and an author cannot be reviewed,
    quarantined or re-run later, which is the entire lifecycle §20 requires.
    ``deterministic`` is the field the review gate reads — a non-deterministic
    check can never be accepted (see :func:`review_check`).
    """

    check_id: str
    description: str
    kind: CheckKind
    target: str
    source_agent: str
    deterministic: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_id", _non_empty(self.check_id, "check_id"))
        object.__setattr__(self, "description", _bounded_text(self.description, "description", MAX_DESCRIPTION_CHARS))
        try:
            object.__setattr__(self, "kind", CheckKind(str(self.kind)))
        except ValueError as error:
            allowed = ", ".join(kind.value for kind in CheckKind)
            raise ValueError(f"unknown kind {self.kind!r}; expected one of: {allowed}") from error
        object.__setattr__(self, "target", _non_empty(self.target, "target"))
        object.__setattr__(self, "source_agent", _non_empty(self.source_agent, "source_agent"))
        if not isinstance(self.deterministic, bool):
            raise ValueError(f"deterministic must be a bool, got {self.deterministic!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "description": self.description,
            "kind": self.kind.value,
            "target": self.target,
            "source_agent": self.source_agent,
            "deterministic": self.deterministic,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeneratedCheck:
        # The constructor re-validates, so a hand-edited document cannot
        # bypass the honesty rules by skipping __post_init__.
        return cls(
            check_id=str(data["check_id"]),
            description=str(data["description"]),
            # Coerced (and validated with the friendly message) in __post_init__.
            kind=data["kind"],
            target=str(data["target"]),
            source_agent=str(data["source_agent"]),
            deterministic=bool(data["deterministic"]),
        )


@dataclass(frozen=True)
class CheckProvenance:
    """The review verdict on one check, with the check it reviewed.

    ``accepted=True`` on a **non-deterministic** check is refused here, so the
    verdict cannot be forged by hand-editing a document or by constructing the
    record directly instead of going through :func:`review_check`. ``reason`` is
    REQUIRED non-empty: a rejection with no stated cause invites someone to
    re-admit the same check next week.
    """

    check: GeneratedCheck
    accepted: bool
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.check, GeneratedCheck):
            raise ValueError(f"check must be a GeneratedCheck, got {self.check!r}")
        if not isinstance(self.accepted, bool):
            raise ValueError(f"accepted must be a bool, got {self.accepted!r}")
        object.__setattr__(self, "reason", _non_empty(self.reason, "reason"))
        if self.accepted and not self.check.deterministic:
            raise ValueError(f"cannot accept non-deterministic check '{self.check.check_id}': a check whose result varies between runs cannot be evidence, so accepting it would let a proposal launder itself into a gate")

    def to_dict(self) -> dict[str, Any]:
        return {"check": self.check.to_dict(), "accepted": self.accepted, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckProvenance:
        return cls(check=GeneratedCheck.from_dict(data["check"]), accepted=bool(data["accepted"]), reason=str(data["reason"]))


def review_check(check: GeneratedCheck) -> CheckProvenance:
    """Review one generated check. Acceptance is a REVIEW, never a pass.

    A **non-deterministic** check is never accepted: plan §20 says every
    generated test is untrusted until reviewed *and executed*, and a check that
    answers differently on the next run cannot be executed into a stable fact —
    it would make the suite a coin flip that a later patch could quietly move
    in its own favour. It is quarantined with that reason attached.

    A deterministic check is accepted as a well-formed, attributable proposal,
    and the reason says plainly that it has still not run.
    """
    if not isinstance(check, GeneratedCheck):
        raise ValueError(f"review_check() expects a GeneratedCheck, got {check!r}")
    if not check.deterministic:
        return CheckProvenance(
            check=check,
            accepted=False,
            reason=(
                f"quarantined: check '{check.check_id}' ({check.kind.value}) is non-deterministic, so it can never be accepted as evidence — "
                "plan §20 keeps generated tests untrusted until reviewed AND executed, and a flaky check has no stable answer to execute"
            ),
        )
    return CheckProvenance(
        check=check,
        accepted=True,
        reason=f"accepted as a well-formed deterministic {check.kind.value} check targeting '{check.target}' from '{check.source_agent}' — this is the REVIEW, not a pass: the check is still a proposal until it actually runs",
    )


# -------------------------------------------------------------- user feedback


@dataclass(frozen=True)
class UserFeedback:
    """One user's correction or preference, as an observation (plan §21).

    ``rating`` is an int in 1..5 and is REFUSED outside that, never clamped —
    a "0" or a "6" is a different signal, not a stronger version of the same
    one. ``task_fingerprint`` is REQUIRED non-empty and is the ONLY task-linked
    field: an opaque caller-supplied id (see ``memory.fingerprint``), never the
    prompt text. ``recorded_at`` is an ISO-8601 stamp refused when it lies in
    the future, because a correction dated ahead of the clock cannot be ordered
    against anything.

    Plan §21 asks for undo/ignore and for preference memory to stay separate
    from objective capability measurement; this record is the separation. The
    ledger stores it, and nothing reads it into a capability estimate.
    """

    feedback_id: str
    rating: int
    task_fingerprint: str
    recorded_at: str
    correction_text: str | None = None
    preferred_capability: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "feedback_id", _non_empty(self.feedback_id, "feedback_id"))
        if isinstance(self.rating, bool) or not isinstance(self.rating, int) or not 1 <= self.rating <= 5:
            raise ValueError(f"rating must be an int in 1..5, got {self.rating!r} (bounds are refused, never clamped)")
        object.__setattr__(self, "task_fingerprint", _bounded_text(self.task_fingerprint, "task_fingerprint", MAX_FINGERPRINT_CHARS))
        # Validated (and refused when future-dated) in _parse_timestamp.
        _parse_timestamp(self.recorded_at, "recorded_at")
        object.__setattr__(self, "recorded_at", str(self.recorded_at))
        if self.correction_text is not None:
            object.__setattr__(self, "correction_text", _bounded_text(self.correction_text, "correction_text", MAX_CORRECTION_CHARS))
        if self.preferred_capability is not None:
            object.__setattr__(self, "preferred_capability", _non_empty(self.preferred_capability, "preferred_capability"))

    def to_dict(self) -> dict[str, Any]:
        # Deliberately no prompt/transcript/path field: private task content is
        # never stored, only the opaque fingerprint above.
        return {
            "feedback_id": self.feedback_id,
            "rating": self.rating,
            "correction_text": self.correction_text,
            "preferred_capability": self.preferred_capability,
            "task_fingerprint": self.task_fingerprint,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserFeedback:
        return cls(
            feedback_id=str(data["feedback_id"]),
            rating=int(data["rating"]),
            task_fingerprint=str(data["task_fingerprint"]),
            recorded_at=str(data["recorded_at"]),
            correction_text=None if data.get("correction_text") is None else str(data["correction_text"]),
            preferred_capability=None if data.get("preferred_capability") is None else str(data["preferred_capability"]),
        )


@dataclass(frozen=True)
class PreferenceUpdate:
    """A preference weight for one capability, with the count behind it.

    ``weight`` lives in [0, 1] and ``reason`` is REQUIRED non-empty, because a
    weight of 0.0 means two very different things — "no preference expressed"
    and "the user actively dislikes this" — and only the sentence separates
    them. This is a PREFERENCE signal (plan §21), never a capability estimate:
    nothing here feeds ``CapabilityEstimate`` or an evidence level.
    """

    capability: str
    weight: float
    sample_count: int
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability", _non_empty(self.capability, "capability"))
        if isinstance(self.weight, bool) or not isinstance(self.weight, (int, float)) or not math.isfinite(float(self.weight)) or not 0.0 <= float(self.weight) <= 1.0:
            raise ValueError(f"weight must be a finite number in [0, 1], got {self.weight!r}")
        object.__setattr__(self, "weight", float(self.weight))
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 0:
            raise ValueError(f"sample_count must be an int >= 0, got {self.sample_count!r}")
        object.__setattr__(self, "reason", _non_empty(self.reason, "reason"))

    def to_dict(self) -> dict[str, Any]:
        return {"capability": self.capability, "weight": self.weight, "sample_count": self.sample_count, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PreferenceUpdate:
        return cls(
            capability=str(data["capability"]),
            weight=float(data["weight"]),
            sample_count=int(data["sample_count"]),
            reason=str(data["reason"]),
        )


class FeedbackStoreError(RuntimeError):
    """The feedback document is unreadable, corrupt, or dishonest."""


#: The reason a below-threshold preference is reported with. Exposed so a caller
#: (and a test) can match on the sentence rather than on a float.
INSUFFICIENT_FEEDBACK_REASON = "insufficient feedback"


class FeedbackLedger:
    """Durable user feedback for one directory: ``feedback.json``.

    ``record()`` appends one :class:`UserFeedback` and persists atomically;
    ``for_capability``/``ratings`` read back only the items that named a
    capability; ``to_capability_preference`` turns those ratings into a
    :class:`PreferenceUpdate`.

    **Private task content is never stored by default.** The only task-linked
    field persisted is the opaque ``task_fingerprint``. A user's own correction
    text is stored because the user typed it, and it is bounded for that reason;
    prompts, transcripts and tool output are not accepted here at all.

    A corrupt document raises :class:`FeedbackStoreError` naming the cause. A
    missing file is genuinely empty — absence is not corruption — but the
    difference is never papered over: a file that cannot be parsed stops the
    ledger instead of being overwritten by the next ``record()``.
    """

    def __init__(self, directory: Path | str) -> None:
        if directory is None:
            raise ValueError("FeedbackLedger requires a directory: this ledger is durable by contract, and an in-memory one under this name would be a lie")
        self._directory = Path(directory)
        self._feedback: list[UserFeedback] = self._load()

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def path(self) -> Path:
        return self._directory / FEEDBACK_FILE_NAME

    def _load(self) -> list[UserFeedback]:
        path = self.path
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError) as error:
            raise FeedbackStoreError(f"feedback ledger '{path}' is unreadable: {error}") from error
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise FeedbackStoreError(f"feedback ledger '{path}' is not valid JSON: {error}") from error
        if not isinstance(document, dict) or not isinstance(document.get("feedback", []), list):
            raise FeedbackStoreError(f"feedback ledger '{path}' must be a JSON object with a 'feedback' list")
        version = document.get("document_version", FEEDBACK_STORE_VERSION)
        if version != FEEDBACK_STORE_VERSION:
            raise FeedbackStoreError(f"feedback ledger '{path}' has document_version {version!r}, but this build reads {FEEDBACK_STORE_VERSION}: refusing to guess at a shape it does not know")
        rows: list[UserFeedback] = []
        for index, entry in enumerate(document["feedback"]):
            try:
                rows.append(UserFeedback.from_dict(entry))
            except (TypeError, KeyError, ValueError) as error:
                raise FeedbackStoreError(f"feedback ledger '{path}' feedback #{index} is invalid: {error}") from error
        return rows

    def save(self) -> None:
        """Atomically rewrite the document (temp + fsync + ``os.replace``)."""
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                json.dump({"document_version": FEEDBACK_STORE_VERSION, "feedback": [item.to_dict() for item in self._feedback]}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # ----------------------------------------------------------------- writes

    def record(self, feedback: UserFeedback) -> None:
        """Store one feedback row and persist it immediately.

        A duplicate ``feedback_id`` is refused: the same correction landing
        twice would be counted twice by :meth:`to_capability_preference`, which
        is exactly the fabricated sample count this module exists to avoid.
        """
        if not isinstance(feedback, UserFeedback):
            raise ValueError(f"record() expects a UserFeedback, got {feedback!r}")
        if any(item.feedback_id == feedback.feedback_id for item in self._feedback):
            raise ValueError(f"duplicate feedback_id '{feedback.feedback_id}': one correction, one row, so the preference weight cannot count it twice")
        self._feedback.append(feedback)
        self.save()

    # ------------------------------------------------------------------ reads

    def all(self) -> tuple[UserFeedback, ...]:
        """Every stored row, in the order it was recorded."""
        return tuple(self._feedback)

    def for_capability(self, capability: str) -> tuple[UserFeedback, ...]:
        """Every row that named ``capability`` in ``preferred_capability``.

        Rows with no ``preferred_capability`` are not "for" any capability: they
        are corrections about a task, not preferences about a competence, and
        folding them into every capability's weight would make an unrelated
        complaint a global opinion.
        """
        wanted = _non_empty(capability, "capability")
        return tuple(item for item in self._feedback if item.preferred_capability == wanted)

    def ratings(self, capability: str) -> tuple[int, ...]:
        """The ratings behind ``capability``'s preference, in record order."""
        return tuple(item.rating for item in self.for_capability(capability))

    def to_capability_preference(self, capability: str) -> PreferenceUpdate:
        """Turn the ratings for one capability into a conservative weight.

        Below :data:`FEEDBACK_MIN_SAMPLES` the weight is ``0.0`` and the reason
        is :data:`INSUFFICIENT_FEEDBACK_REASON` — a handful of clicks is not a
        preference the router should act on (plan §21: each item is a
        categorised observation, and preference memory stays separate from
        objective capability measurement).

        At or above the floor the weight maps the mean rating from 1..5 onto
        0..1 as ``(mean - 1) / 4``, so a 3 ("fine") lands exactly on 0.5 —
        neutral stays neutral instead of being read as mild approval. The reason
        carries the count and the mean so a caller can see what the number rests
        on.
        """
        wanted = _non_empty(capability, "capability")
        ratings: tuple[int, ...] = self.ratings(wanted)
        count = len(ratings)
        if count < FEEDBACK_MIN_SAMPLES:
            return PreferenceUpdate(capability=wanted, weight=0.0, sample_count=count, reason=INSUFFICIENT_FEEDBACK_REASON)
        mean = sum(ratings) / count
        weight = (mean - 1.0) / 4.0
        return PreferenceUpdate(
            capability=wanted,
            weight=weight,
            sample_count=count,
            reason=f"{count} rating(s) at or above FEEDBACK_MIN_SAMPLES {FEEDBACK_MIN_SAMPLES}, mean {mean:.2f} of 5 mapped onto [0, 1]; a preference signal for routing, not a capability measurement (plan §21)",
        )


__all__ = [
    "FEEDBACK_FILE_NAME",
    "FEEDBACK_MIN_SAMPLES",
    "FEEDBACK_STORE_VERSION",
    "INSUFFICIENT_FEEDBACK_REASON",
    "MAX_CORRECTION_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_FINGERPRINT_CHARS",
    "CheckKind",
    "CheckProvenance",
    "FeedbackLedger",
    "FeedbackStoreError",
    "GeneratedCheck",
    "PreferenceUpdate",
    "UserFeedback",
    "review_check",
]
