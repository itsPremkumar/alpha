"""Shadow routing, canary policy and router self-evaluation (plan §13, Feature L).

The router is software, and software regresses. Feature L wants to know that
*before* a routing change reaches a normal task. This module is that idea
reduced to what can honestly be said without a live deployment:

* a :class:`ShadowComparison` is ONE side-by-side record — the assignment the
  real router made, the assignment a CANDIDATE router would have made, and
  what each side predicted;
* a :class:`ShadowReport` aggregates those records and discloses, in
  ``dataset_note``, that **no frozen replay dataset exists yet** (plan §13.2),
  so every count it carries is a count over the comparisons it holds;
* a :class:`CanaryPolicy` names the rollout envelope (slice, allowed task
  categories, sample floor) and the known-good ``baseline_policy`` to roll
  back to, and refuses to promote anything by itself — §13.3: the router never
  rewrites its own production policy, so promotion is a CALLER decision.

Four rules decide the shape of this module:

* **PREDICTION ONLY. Nothing here spends model budget.** The candidate router
  is an injected callable the CALLER runs; this module stores its answers and
  never calls a model, never dispatches work and never changes a live
  assignment.
* **``agreement_rate`` is ``None`` with zero comparisons — never 0.0.** A
  report over no comparisons has nothing to say, and 0.0 would be a
  measurement nobody took. :class:`ShadowReport` refuses a numeric rate when
  it carries no comparisons, so a hand-built report cannot state the lie the
  runner never would.
* **``better_calls`` needs a MEASURED outcome.** A predicted quality delta is
  a prediction, not an outcome, so it can never promote a candidate on its
  own: :attr:`ShadowComparison.measured_quality_delta` is the only field that
  counts, and until plan §13.2's frozen replay dataset exists it is ``None``
  and the comparison is ``undecided``.
* **Every comparison is counted exactly once.** ``better_calls +
  worse_calls + undecided`` must equal ``len(comparisons)``, and the
  ``agreement_rate`` must be the rate the carried comparisons actually
  produce — a report whose summary disagrees with its own rows is refused by
  name.

:class:`ShadowRunner` is durable JSON using the package's atomic write
pattern (temp + fsync + ``os.replace``); a corrupt document raises
:class:`ShadowStoreError` rather than starting empty and later writing
amnesia over the file. Single-process JSON: atomic against a crash in one
process, **never cross-process exactly-once**.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The single JSON document a durable :class:`ShadowRunner` owns.
SHADOW_FILE_NAME = "shadow.json"

#: The REQUIRED disclosure every :class:`ShadowReport` carries. Plan §13.2's
#: frozen, versioned evaluation dataset does not exist yet, so a shadow report
#: can only describe the comparisons it actually holds.
NO_REPLAY_DATASET_NOTE = "no frozen replay dataset exists yet (plan §13.2): this report describes only the comparisons it holds, and quality at fixed cost / routing regret / escalation rates cannot be computed from them"

#: The minimum fraction of shadow comparisons a candidate must agree on with
#: the production router before it is even a *canary*. Below this the
#: candidate is not a variant of the router, it is a different router.
MIN_AGREEMENT_FOR_PROMOTE = 0.5

#: The default sample floor before a promotion decision may be considered at
#: all. A documented starting policy, to be tuned against measured outcomes.
DEFAULT_MIN_SAMPLE_BEFORE_PROMOTE = 20

#: The keys an assignment dict may carry its primary passport id under.
_PASSPORT_ID_KEYS = ("passport_id", "entity_id")


def _primary_passport_id(assignment: Mapping[str, Any]) -> str | None:
    """The passport id one assignment dict names, or None.

    ``MicrotaskAssignment.to_dict()`` uses ``passport_id``; a caller-built
    projection may use ``entity_id``. Anything else means the row cannot be
    compared, which is reported rather than guessed.
    """
    for key in _PASSPORT_ID_KEYS:
        value = assignment.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _optional_finite(value: Any, field_name: str) -> float | None:
    """Validate an optional finite float that may be negative (a delta)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{field_name} must be a finite number or None, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class ShadowComparison:
    """One shadow comparison: what the real router chose vs what a candidate would.

    ``primary_assignment`` and ``candidate_assignment`` are the two assignment
    projections to compare (a ``MicrotaskAssignment.to_dict()``, or any
    caller-built mapping carrying a passport id under ``passport_id`` or
    ``entity_id``). ``agrees`` is DERIVED from those two passport ids — same
    primary, agreement — and is never stored, so a hand-edited document cannot
    claim an agreement the rows do not carry.

    ``predicted_quality_delta`` is the candidate's own prediction
    (candidate − primary); it is a PREDICTION and can never promote anything.
    ``measured_quality_delta`` is the measured outcome difference when one
    exists, and it is the only field ``ShadowReport`` counts into
    ``better_calls``/``worse_calls``.
    """

    task_fingerprint: str
    task_category: str
    profile_version: str
    primary_assignment: dict[str, Any]
    candidate_assignment: dict[str, Any]
    predicted_quality_delta: float | None = None
    measured_quality_delta: float | None = None
    # Derived from the two assignments; never an input, never serialised.
    agrees: bool = field(init=False, default=False)
    reason: str = ""

    def __post_init__(self) -> None:
        for field_name in ("task_fingerprint", "task_category", "profile_version"):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be non-empty")
        for field_name in ("primary_assignment", "candidate_assignment"):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping) or not value:
                raise ValueError(f"{field_name} must be a non-empty mapping: an assignment with no content cannot be compared")
            object.__setattr__(self, field_name, dict(value))
        object.__setattr__(self, "predicted_quality_delta", _optional_finite(self.predicted_quality_delta, "predicted_quality_delta"))
        object.__setattr__(self, "measured_quality_delta", _optional_finite(self.measured_quality_delta, "measured_quality_delta"))

        primary_id = _primary_passport_id(self.primary_assignment)
        candidate_id = _primary_passport_id(self.candidate_assignment)
        agrees = primary_id is not None and candidate_id is not None and primary_id == candidate_id
        object.__setattr__(self, "agrees", agrees)
        if not self.reason:
            if primary_id is None or candidate_id is None:
                object.__setattr__(self, "reason", "cannot compare: an assignment mapping carries no passport id (expected 'passport_id' or 'entity_id')")
            elif agrees:
                object.__setattr__(self, "reason", f"same primary '{primary_id}': the candidate router would have made the same choice")
            else:
                object.__setattr__(self, "reason", f"different primary: production chose '{primary_id}', the candidate would have chosen '{candidate_id}'")

    def to_dict(self) -> dict[str, Any]:
        # ``agrees`` is deliberately absent: it is derived on construction
        # from the two assignment mappings, never a claim a document makes.
        return {
            "task_fingerprint": self.task_fingerprint,
            "task_category": self.task_category,
            "profile_version": self.profile_version,
            "primary_assignment": dict(self.primary_assignment),
            "candidate_assignment": dict(self.candidate_assignment),
            "predicted_quality_delta": self.predicted_quality_delta,
            "measured_quality_delta": self.measured_quality_delta,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShadowComparison:
        # The constructor re-validates, so a hand-edited document cannot
        # bypass the honesty rules by skipping __post_init__.
        return cls(
            task_fingerprint=str(data["task_fingerprint"]),
            task_category=str(data["task_category"]),
            profile_version=str(data["profile_version"]),
            primary_assignment=dict(data["primary_assignment"]),
            candidate_assignment=dict(data["candidate_assignment"]),
            predicted_quality_delta=data.get("predicted_quality_delta"),
            measured_quality_delta=data.get("measured_quality_delta"),
            reason=str(data.get("reason", "")),
        )


def _summarise(comparisons: tuple[ShadowComparison, ...]) -> tuple[float | None, int, int, int]:
    """The report figures a tuple of comparisons actually supports.

    ``agreement_rate`` is ``None`` for an empty tuple — never 0.0. A measured
    tie (``measured_quality_delta == 0.0``) is measured but is neither better
    nor worse, so it is counted ``undecided`` rather than dropped.
    """
    if not comparisons:
        return None, 0, 0, 0
    agreements = sum(1 for comparison in comparisons if comparison.agrees)
    better = sum(1 for comparison in comparisons if comparison.measured_quality_delta is not None and comparison.measured_quality_delta > 0.0)
    worse = sum(1 for comparison in comparisons if comparison.measured_quality_delta is not None and comparison.measured_quality_delta < 0.0)
    undecided = len(comparisons) - better - worse
    return agreements / len(comparisons), better, worse, undecided


@dataclass(frozen=True)
class ShadowReport:
    """The aggregate of one set of shadow comparisons, with its limits named.

    ``dataset_note`` is REQUIRED and carries :data:`NO_REPLAY_DATASET_NOTE`:
    without plan §13.2's frozen dataset, this report can only describe the
    comparisons it holds — it cannot compute quality at fixed cost, routing
    regret or unnecessary-escalation rates.

    ``better_calls``/``worse_calls``/``undecided`` are validated against the
    carried ``comparisons``: the three must partition them exactly
    (``better + worse + undecided == len(comparisons)``), and ``agreement_rate``
    must be the rate those comparisons produce (``None`` only when there are
    none). A summary that disagrees with its own rows is refused by name — and
    over zero comparisons a numeric ``agreement_rate`` is refused too, because
    "agreement 0%" over nothing is a measurement nobody took.
    """

    comparisons: tuple[ShadowComparison, ...]
    agreement_rate: float | None
    better_calls: int
    worse_calls: int
    undecided: int
    profile_version: str
    dataset_note: str

    def __post_init__(self) -> None:
        comparisons = tuple(self.comparisons)
        for comparison in comparisons:
            if not isinstance(comparison, ShadowComparison):
                raise ValueError(f"comparisons must contain ShadowComparison rows, got {comparison!r}")
        object.__setattr__(self, "comparisons", comparisons)
        for field_name in ("better_calls", "worse_calls", "undecided"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be an int >= 0, got {value!r}")
        if self.better_calls + self.worse_calls + self.undecided != len(comparisons):
            raise ValueError(
                f"better_calls {self.better_calls} + worse_calls {self.worse_calls} + undecided {self.undecided} = "
                f"{self.better_calls + self.worse_calls + self.undecided}, but the report carries {len(comparisons)} comparison(s): every comparison is counted exactly once"
            )
        for field_name in ("profile_version", "dataset_note"):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be non-empty: a report without its rubric or its honest limits is an assertion")
        expected_rate, expected_better, expected_worse, _ = _summarise(comparisons)
        if self.agreement_rate is None:
            if expected_rate is not None:
                raise ValueError(f"agreement_rate must be {expected_rate:.3f} for the {len(comparisons)} comparison(s) carried, got None")
        else:
            if isinstance(self.agreement_rate, bool) or not isinstance(self.agreement_rate, (int, float)) or not math.isfinite(float(self.agreement_rate)) or not 0.0 <= float(self.agreement_rate) <= 1.0:
                raise ValueError(f"agreement_rate must be a finite number in [0, 1] or None, got {self.agreement_rate!r}")
            # Zero comparisons has ONE honest answer: None. A numeric rate over
            # no comparisons (0.0 included) is a measurement nobody took, so it
            # is refused rather than reconciled against an invented 0.0 baseline.
            if expected_rate is None:
                raise ValueError(f"agreement_rate must be None for a report carrying 0 comparison(s): there is no agreement to measure, so {float(self.agreement_rate):.3f} is a measurement nobody took (never 0.0 at zero)")
            if abs(float(self.agreement_rate) - expected_rate) > 1e-9:
                raise ValueError(f"agreement_rate {float(self.agreement_rate):.3f} contradicts the {len(comparisons)} comparison(s) carried, which agree on {expected_rate:.3f}")
        if (self.better_calls, self.worse_calls) != (expected_better, expected_worse):
            raise ValueError(f"better_calls {self.better_calls}/worse_calls {self.worse_calls} contradict the carried comparisons, which show {expected_better}/{expected_worse}: the counts are derived from the rows, never hand-set")

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparisons": [comparison.to_dict() for comparison in self.comparisons],
            "agreement_rate": self.agreement_rate,
            "better_calls": self.better_calls,
            "worse_calls": self.worse_calls,
            "undecided": self.undecided,
            "profile_version": self.profile_version,
            "dataset_note": self.dataset_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShadowReport:
        return cls(
            comparisons=tuple(ShadowComparison.from_dict(entry) for entry in data.get("comparisons", ())),
            agreement_rate=data.get("agreement_rate"),
            better_calls=int(data.get("better_calls", 0)),
            worse_calls=int(data.get("worse_calls", 0)),
            undecided=int(data.get("undecided", 0)),
            profile_version=str(data["profile_version"]),
            dataset_note=str(data["dataset_note"]),
        )


class ShadowStoreError(RuntimeError):
    """The shadow document is unreadable, corrupt, or dishonest."""


class ShadowRunner:
    """Records shadow comparisons and reports on them. Predictions, never budget.

    One runner is one directory holding one ``shadow.json``. Constructed
    WITHOUT a directory the runner is purely in-memory. ``record()`` stores a
    :class:`ShadowComparison` and, when durable, persists it atomically.

    The candidate router is **injected by the caller**: this module never
    calls a model, never spends budget and never changes a live assignment.
    It is a place to keep the side-by-side record so a maintainer can decide
    about a promotion.
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self._directory = Path(directory) if directory is not None else None
        self._comparisons: list[ShadowComparison] = self._load()

    @property
    def directory(self) -> Path | None:
        return self._directory

    @property
    def durable(self) -> bool:
        """True when this runner persists to a directory."""
        return self._directory is not None

    @property
    def path(self) -> Path | None:
        return None if self._directory is None else self._directory / SHADOW_FILE_NAME

    def _load(self) -> list[ShadowComparison]:
        path = self.path
        if path is None:
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError) as error:
            raise ShadowStoreError(f"shadow runner '{path}' is unreadable: {error}") from error
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise ShadowStoreError(f"shadow runner '{path}' is not valid JSON: {error}") from error
        if not isinstance(document, dict) or not isinstance(document.get("comparisons", []), list):
            raise ShadowStoreError(f"shadow runner '{path}' must be a JSON object with a 'comparisons' list")
        rows: list[ShadowComparison] = []
        for index, entry in enumerate(document["comparisons"]):
            try:
                rows.append(ShadowComparison.from_dict(entry))
            except (TypeError, KeyError, ValueError) as error:
                raise ShadowStoreError(f"shadow runner '{path}' comparison #{index} is invalid: {error}") from error
        return rows

    def save(self) -> None:
        """Atomically rewrite the document (temp + fsync + ``os.replace``)."""
        if self._directory is None:
            raise ValueError("this ShadowRunner has no directory: an in-memory runner cannot be persisted (construct one with a directory)")
        path = self._directory / SHADOW_FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                json.dump({"comparisons": [comparison.to_dict() for comparison in self._comparisons]}, handle, ensure_ascii=False)
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

    def record(self, comparison: ShadowComparison) -> None:
        """Store one comparison (a durable runner persists it immediately)."""
        if not isinstance(comparison, ShadowComparison):
            raise ValueError(f"record() expects a ShadowComparison, got {comparison!r}")
        self._comparisons.append(comparison)
        if self._directory is not None:
            self.save()

    # ------------------------------------------------------------------ reads

    def comparisons(self, *, profile_version: str | None = None, task_category: str | None = None) -> tuple[ShadowComparison, ...]:
        """Every stored comparison, deterministically sorted.

        ``profile_version`` scopes the read by the comparability rule (a
        rubric change invalidates comparability); ``task_category`` narrows it
        further. With neither, everything is returned.
        """
        selected: Iterable[ShadowComparison] = self._comparisons
        if profile_version is not None:
            if not profile_version:
                raise ValueError("profile_version filter must be non-empty when given (pass None for 'no filter')")
            selected = [comparison for comparison in selected if comparison.profile_version == profile_version]
        if task_category is not None:
            if not task_category:
                raise ValueError("task_category filter must be non-empty when given (pass None for 'no filter')")
            selected = [comparison for comparison in selected if comparison.task_category == task_category]
        return tuple(sorted(selected, key=lambda comparison: (comparison.task_fingerprint, comparison.task_category, comparison.profile_version, comparison.reason)))

    def report(self, profile_version: str) -> ShadowReport:
        """Aggregate the comparisons recorded under exactly one ``profile_version``.

        The comparability rule holds: only comparisons measured under this
        rubric are aggregated. With none, ``agreement_rate`` is ``None``
        (never 0.0), all three counts are 0, and the ``dataset_note`` says so
        alongside :data:`NO_REPLAY_DATASET_NOTE`.
        """
        if not profile_version:
            raise ValueError("report() requires a non-empty profile_version (no score without its profile version)")
        selected = self.comparisons(profile_version=profile_version)
        rate, better, worse, undecided = _summarise(selected)
        with_outcome = sum(1 for comparison in selected if comparison.measured_quality_delta is not None)
        note = f"{NO_REPLAY_DATASET_NOTE}; {len(selected)} comparison(s) under profile_version '{profile_version}', {with_outcome} of which carry a measured outcome"
        return ShadowReport(
            comparisons=selected,
            agreement_rate=rate,
            better_calls=better,
            worse_calls=worse,
            undecided=undecided,
            profile_version=profile_version,
            dataset_note=note,
        )


@dataclass(frozen=True)
class CanaryPolicy:
    """The rollout envelope for one candidate router (plan §13.3).

    ``enabled`` gates the whole thing. ``slice`` is the fraction of safe
    low-risk requests the candidate may serve — refused outside [0, 1],
    never clamped. ``allowed_task_categories`` names the categories the canary
    may touch (empty means "no category restriction declared", said plainly
    rather than silently). ``min_sample_before_promote`` is the shadow sample
    floor: 0 is allowed and means "no floor declared", and the caller still
    owns the decision. ``baseline_policy`` is the known-good policy to roll back
    to and ``rollback`` names how — both REQUIRED, because §13.3 demands instant
    rollback to a known policy version and a canary without one is a plan to
    get stuck.

    This record deliberately does NOT name a candidate router: §13.3 forbids
    the router from rewriting its own production policy, so promotion is the
    CALLER's decision and :meth:`refuse_promotion` says so by name.
    """

    enabled: bool = False
    slice: float = 0.0
    allowed_task_categories: tuple[str, ...] = ()
    min_sample_before_promote: int = DEFAULT_MIN_SAMPLE_BEFORE_PROMOTE
    baseline_policy: str = ""
    rollback: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError(f"enabled must be a bool, got {self.enabled!r}")
        if isinstance(self.slice, bool) or not isinstance(self.slice, (int, float)) or not math.isfinite(float(self.slice)) or not 0.0 <= float(self.slice) <= 1.0:
            raise ValueError(f"slice must be a finite number in [0, 1], got {self.slice!r} (bounds are refused, never clamped)")
        object.__setattr__(self, "slice", float(self.slice))
        categories = tuple(self.allowed_task_categories)
        for category in categories:
            if not category:
                raise ValueError("allowed_task_categories entries must be non-empty strings")
        object.__setattr__(self, "allowed_task_categories", tuple(dict.fromkeys(categories)))
        if isinstance(self.min_sample_before_promote, bool) or not isinstance(self.min_sample_before_promote, int) or self.min_sample_before_promote < 0:
            raise ValueError(f"min_sample_before_promote must be an int >= 0, got {self.min_sample_before_promote!r} (bounds are refused, never clamped)")
        for field_name in ("baseline_policy", "rollback"):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be non-empty: a canary without a named baseline to roll back to is a plan to get stuck (plan §13.3)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "slice": self.slice,
            "allowed_task_categories": list(self.allowed_task_categories),
            "min_sample_before_promote": self.min_sample_before_promote,
            "baseline_policy": self.baseline_policy,
            "rollback": self.rollback,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CanaryPolicy:
        return cls(
            enabled=bool(data.get("enabled", False)),
            slice=float(data.get("slice", 0.0)),
            allowed_task_categories=tuple(str(category) for category in data.get("allowed_task_categories", ())),
            min_sample_before_promote=int(data.get("min_sample_before_promote", DEFAULT_MIN_SAMPLE_BEFORE_PROMOTE)),
            baseline_policy=str(data["baseline_policy"]),
            rollback=str(data["rollback"]),
        )

    def covers_task_category(self, task_category: str) -> bool:
        """Is this category inside the declared canary envelope?

        An empty ``allowed_task_categories`` means NO restriction was
        declared — which is disclosed in the reason rather than presented as
        "everything is allowed".
        """
        if not task_category:
            raise ValueError("task_category must be non-empty")
        if not self.allowed_task_categories:
            return True
        return task_category in self.allowed_task_categories

    def refuse_promotion(self) -> str:
        """The standing refusal: THIS object cannot promote a policy at all.

        Plan §13.3 — "Do not allow the router to rewrite its own production
        policy without a maintainer-approved release path." The record names
        the baseline it would roll back to and the rollback it would use, and
        names nothing it could promote: a candidate router is the caller's to
        install, and the decision is the caller's to make.
        """
        return f"canary promotion is a CALLER decision: this policy names the baseline '{self.baseline_policy}' and the rollback '{self.rollback}' but no candidate router, so nothing here may rewrite a production policy (plan §13.3)"

    def promotion_reason(self, report: ShadowReport) -> str:
        """Why this report does — or does not — earn a promotion decision.

        The gate reads ONLY the report's own numbers: enough shadow
        comparisons (``min_sample_before_promote``), enough agreement with the
        production router (``MIN_AGREEMENT_FOR_PROMOTE``), and a candidate
        that would have WON more measured calls than it lost. A tie or a
        missing outcome is not a win.
        """
        if not isinstance(report, ShadowReport):
            raise ValueError(f"report must be a ShadowReport, got {report!r}")
        if not self.enabled:
            return "canary promotion refused: the canary is disabled"
        if report.agreement_rate is None:
            return f"canary promotion refused: no shadow comparisons exist under profile_version '{report.profile_version}' (agreement_rate is None, never 0.0)"
        outside = sorted({comparison.task_category for comparison in report.comparisons if not self.covers_task_category(comparison.task_category)})
        if outside:
            return f"canary promotion refused: task category/categories {outside} are outside allowed_task_categories {list(self.allowed_task_categories)}"
        if len(report.comparisons) < self.min_sample_before_promote:
            return f"canary promotion refused: {len(report.comparisons)} comparison(s) is below min_sample_before_promote {self.min_sample_before_promote}"
        if report.agreement_rate < MIN_AGREEMENT_FOR_PROMOTE:
            return f"canary promotion refused: agreement {report.agreement_rate:.3f} is below MIN_AGREEMENT_FOR_PROMOTE {MIN_AGREEMENT_FOR_PROMOTE}"
        if report.better_calls == 0:
            return f"canary promotion refused: better_calls is 0 — no measured outcome shows the candidate winning ({report.undecided} undecided, and a predicted delta never counts)"
        if report.better_calls <= report.worse_calls:
            return f"canary promotion refused: better_calls {report.better_calls} does not beat worse_calls {report.worse_calls} on the report's own measured numbers"
        return (
            f"canary MAY be considered for promotion: {len(report.comparisons)} comparison(s) >= {self.min_sample_before_promote}, "
            f"agreement {report.agreement_rate:.3f}, better_calls {report.better_calls} beats worse_calls {report.worse_calls}; "
            f"promotion remains the caller's decision and {self.rollback} rolls back to baseline '{self.baseline_policy}'"
        )

    def may_promote(self, report: ShadowReport) -> tuple[bool, str]:
        """``(may_promote, reason)`` — the gate plus the sentence behind it.

        ``True`` only when the report's own numbers clear every gate (enough
        comparisons, enough agreement with production, a measured win). Even
        then this is not a promotion: :meth:`refuse_promotion` still applies
        and the caller decides. The reason is returned rather than logged so a
        refusal travels with its cause instead of being a bare ``False``.
        """
        if not isinstance(report, ShadowReport):
            raise ValueError(f"report must be a ShadowReport, got {report!r}")
        reason = self.promotion_reason(report)
        return reason.startswith("canary MAY"), reason


__all__ = [
    "DEFAULT_MIN_SAMPLE_BEFORE_PROMOTE",
    "MIN_AGREEMENT_FOR_PROMOTE",
    "NO_REPLAY_DATASET_NOTE",
    "SHADOW_FILE_NAME",
    "CanaryPolicy",
    "ShadowComparison",
    "ShadowReport",
    "ShadowRunner",
    "ShadowStoreError",
]
