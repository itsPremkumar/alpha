"""Core types for the continual-intelligence layer.

Four ideas are declared here and nowhere else, because every other module in
:mod:`alpha.intelligence` reads them from this one place:

* :class:`LearningMode` — the single severity-ordered posture enum. It exists
  because Alpha already had five *ad-hoc* switches (``evolution_evidence.enabled``,
  ``self_tuning.enabled``, ``autonomous_mode``, ``skill_curator_tick(dry_run)``,
  the ``local_digest`` executor) that nobody could read back as one state. This
  enum composes them; it does not replace them.
* :class:`ExpertIdentity` — a **stable** learned-entity identity that survives
  restart, paging, pruning and migration. Array position is never identity; the
  id is allocated from a monotonic counter persisted beside the record.
* :class:`ExpertLifecycleState` — the expert state machine, including the
  ``PRUNED`` terminal state and the protection that keeps a protected expert out
  of pruning regardless of score.
* :class:`ExpertMetrics` — the measured counters the utility scorer reads.

Honesty rules baked into the models
-----------------------------------
* :attr:`ExpertMetrics.samples` is the only denominator a rate is allowed to
  use. A rate computed over zero samples is ``None``, never ``0.0``, because
  "never observed" and "always failed" are opposite claims.
* Every dataclass here round-trips through ``to_dict``/``from_dict`` with
  explicit validation, so a corrupt persisted document fails loudly at load
  instead of degrading into a plausible-looking default.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "LearningMode",
    "ExpertLifecycleState",
    "ExpertStatus",
    "ReplayStratum",
    "TERMINAL_EXPERT_STATES",
    "PROMOTABLE_FROM",
    "PRUNE_ELIGIBLE_FROM",
    "ExpertIdentity",
    "ExpertMetrics",
    "ExpertRecord",
    "LearningEvent",
    "ExperienceTelemetry",
]


class LearningMode(StrEnum):
    """How much the intelligence layer is permitted to do.

    Ordered by severity in :data:`alpha.intelligence.config._MODE_RANK`. The
    default is :attr:`OBSERVE_ONLY`: record what would happen, change nothing.
    """

    OBSERVE_ONLY = "OBSERVE_ONLY"
    """Read real state, compute candidate decisions, write no learned state."""

    DRY_RUN = "DRY_RUN"
    """Additionally answer "what would change?" for a specific input set."""

    EVALUATE_ONLY = "EVALUATE_ONLY"
    """Additionally *run* evaluations and regressions against a candidate."""

    TRIAL = "TRIAL"
    """Additionally let candidate experts execute on real work, still unpromoted."""

    LEARN = "LEARN"
    """Additionally record learning events and update learned state in place."""

    PROMOTE = "PROMOTE"
    """The only mode that may advance a candidate into the active set."""


class ExpertLifecycleState(StrEnum):
    """Expert state machine (prompt §20).

    ``PROPOSED -> INITIALIZING -> TRIAL -> EVALUATING -> PROMOTED -> ACTIVE``,
    with ``UNDERUSED`` and ``PRUNE_CANDIDATE`` reachable from ``ACTIVE``, and
    ``ARCHIVED`` / ``PRUNED`` as the outcomes.
    """

    PROPOSED = "PROPOSED"
    """A capability gap was detected. Nothing has been built yet."""

    INITIALIZING = "INITIALIZING"
    """The record exists and is being materialised."""

    TRIAL = "TRIAL"
    """Executable on real work, explicitly NOT part of the active set. This is
    the state a dynamically created expert is born into and the only state from
    which promotion is possible once observed."""

    EVALUATING = "EVALUATING"
    """Under measurement against the regression and holdout suites."""

    PROMOTED = "PROMOTED"
    """Passed its gates. Transition to ``ACTIVE`` follows."""

    ACTIVE = "ACTIVE"
    """In the routable set."""

    UNDERUSED = "UNDERUSED"
    """Active but below the recent-usefulness threshold. Not yet prune-eligible."""

    PRUNE_CANDIDATE = "PRUNE_CANDIDATE"
    """Eligible for archiving, subject to grace period and protection."""

    ARCHIVED = "ARCHIVED"
    """Retained on disk with lineage intact, removed from the routable set."""

    PRUNED = "PRUNED"
    """Terminal. The record and its lineage survive; it can be reinstated."""


#: States from which no further transition is legal.
TERMINAL_EXPERT_STATES: frozenset[ExpertLifecycleState] = frozenset({ExpertLifecycleState.PRUNED})

#: Legal transitions. Anything not listed here is refused by
#: :meth:`ExpertRecord.transition` — an unlisted edge is a bug, not a shortcut.
_LEGAL_TRANSITIONS: dict[ExpertLifecycleState, frozenset[ExpertLifecycleState]] = {
    ExpertLifecycleState.PROPOSED: frozenset({ExpertLifecycleState.INITIALIZING, ExpertLifecycleState.ARCHIVED}),
    ExpertLifecycleState.INITIALIZING: frozenset({ExpertLifecycleState.TRIAL, ExpertLifecycleState.ARCHIVED}),
    ExpertLifecycleState.TRIAL: frozenset({ExpertLifecycleState.EVALUATING, ExpertLifecycleState.PROMOTED, ExpertLifecycleState.ARCHIVED}),
    ExpertLifecycleState.EVALUATING: frozenset({ExpertLifecycleState.PROMOTED, ExpertLifecycleState.TRIAL, ExpertLifecycleState.ARCHIVED}),
    ExpertLifecycleState.PROMOTED: frozenset({ExpertLifecycleState.ACTIVE, ExpertLifecycleState.ARCHIVED}),
    ExpertLifecycleState.ACTIVE: frozenset({ExpertLifecycleState.UNDERUSED, ExpertLifecycleState.PRUNE_CANDIDATE, ExpertLifecycleState.ARCHIVED}),
    ExpertLifecycleState.UNDERUSED: frozenset({ExpertLifecycleState.ACTIVE, ExpertLifecycleState.PRUNE_CANDIDATE, ExpertLifecycleState.ARCHIVED}),
    ExpertLifecycleState.PRUNE_CANDIDATE: frozenset({ExpertLifecycleState.ARCHIVED, ExpertLifecycleState.ACTIVE, ExpertLifecycleState.PRUNED}),
    ExpertLifecycleState.ARCHIVED: frozenset({ExpertLifecycleState.INITIALIZING, ExpertLifecycleState.TRIAL, ExpertLifecycleState.PRUNED}),
    ExpertLifecycleState.PRUNED: frozenset({ExpertLifecycleState.ARCHIVED}),
}

#: States a promotion may legally originate from.
PROMOTABLE_FROM: frozenset[ExpertLifecycleState] = frozenset({ExpertLifecycleState.TRIAL, ExpertLifecycleState.EVALUATING})

#: States from which pruning may legally be considered.
PRUNE_ELIGIBLE_FROM: frozenset[ExpertLifecycleState] = frozenset({ExpertLifecycleState.ACTIVE, ExpertLifecycleState.UNDERUSED})


class ReplayStratum(StrEnum):
    """Replay reservoir strata (prompt §7).

    Every stratum is a *reason to replay*, not a tag. A run can belong to
    several, which is why :attr:`ReplayItem.strata` is a set.
    """

    RECENT = "recent"
    HIGH_VALUE = "high_value"
    RARE_FAILURE = "rare_failure"
    REGRESSION = "regression"
    STRATEGY = "strategy"
    TOOL_RECOVERY = "tool_recovery"
    CODING_FAILURE = "coding_failure"
    GIT_FAILURE = "git_failure"
    MEMORY_FAILURE = "memory_failure"
    COORDINATION_FAILURE = "coordination_failure"
    SECURITY_FAILURE = "security_failure"
    OLD_KNOWLEDGE = "old_knowledge"


class ExpertStatus(StrEnum):
    """Alias kept for readability at call sites that talk about "status"."""

    ACTIVE = "ACTIVE"
    TRIAL = "TRIAL"
    ARCHIVED = "ARCHIVED"


@dataclass
class ExpertMetrics:
    """Measured counters for one expert. Every rate is ``None`` until observed."""

    usage: int = 0
    successes: int = 0
    failures: int = 0
    quality_gain_total: float = 0.0
    failure_recoveries: int = 0
    latency_ms_total: float = 0.0
    cost_total: float = 0.0
    regression_impact: float = 0.0
    recent: list[bool] = field(default_factory=list)
    """Booleans (did this observation succeed?) capped at ``experts.recent_window``."""

    @property
    def samples(self) -> int:
        """Total observations. The only legal denominator for a rate."""
        return self.successes + self.failures

    @property
    def success_rate(self) -> float | None:
        """Fraction of observed uses that succeeded, or ``None`` if never observed."""
        if self.samples <= 0:
            return None
        return self.successes / self.samples

    @property
    def mean_latency_ms(self) -> float | None:
        if self.samples <= 0:
            return None
        return self.latency_ms_total / self.samples

    @property
    def mean_cost(self) -> float | None:
        if self.samples <= 0:
            return None
        return self.cost_total / self.samples

    @property
    def quality_gain(self) -> float:
        """Mean measured quality gain, or ``0.0`` before any observation.

        Unlike the rates this is a *sum over zero samples*, which is genuinely
        0.0 rather than unknown — a total improvement of nothing is known.
        """
        if self.samples <= 0:
            return 0.0
        return self.quality_gain_total / self.samples

    @property
    def recent_success_rate(self) -> float | None:
        """Success rate over the capped recent window, or ``None`` if empty."""
        if not self.recent:
            return None
        return sum(1 for ok in self.recent if ok) / len(self.recent)

    def observe(
        self,
        *,
        success: bool,
        quality_gain: float = 0.0,
        latency_ms: float = 0.0,
        cost: float = 0.0,
        recovered: bool = False,
        regression_impact: float = 0.0,
        recent_window: int = 5,
    ) -> None:
        """Record one observation. This is the only mutation path for counters."""
        window = max(1, int(recent_window))
        self.usage += 1
        if success:
            self.successes += 1
        else:
            self.failures += 1
        if recovered:
            self.failure_recoveries += 1
        self.quality_gain_total += float(quality_gain)
        self.latency_ms_total += max(0.0, float(latency_ms))
        self.cost_total += max(0.0, float(cost))
        self.regression_impact += float(regression_impact)
        self.recent.append(bool(success))
        if len(self.recent) > window:
            del self.recent[: len(self.recent) - window]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExpertMetrics:
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"ExpertMetrics has unknown field(s): {unknown}")
        return cls(**dict(data))


@dataclass
class ExpertIdentity:
    """The stable identity of a learned expert (prompt §15/§16).

    ``expert_id`` is allocated from a persisted monotonic counter, formatted
    ``expert_%06d``, and is **never** an array index. The same record keeps its
    id across restart (reload from disk), paging (evict then reload), pruning
    (archived then reinstated), and migration (the field name is the contract).
    """

    expert_id: str
    kind: str = "agent"
    """``agent`` | ``skill`` | ``model_adapter`` | ``routing_policy`` | ``specialist``."""

    capability: str = ""
    """The capability slug this expert was created to fill."""

    generation: int = 0
    """0 for a declared/catalog-backed expert, >=1 for a dynamically grown one."""

    parents: list[str] = field(default_factory=list)
    """Parent expert ids. Empty means "not derived from another expert"."""

    reason: str = ""
    """Why this expert exists. Required for a dynamically grown expert."""

    created_at: str = ""
    status: ExpertLifecycleState = ExpertLifecycleState.PROPOSED
    version: str = "1"
    protected: bool = False
    """Protected experts are never prune-eligible. Read from ``experts.protected``."""

    def __post_init__(self) -> None:
        self.expert_id = (self.expert_id or "").strip()
        if not self.expert_id:
            raise ValueError("expert_id must be a non-empty string")
        if self.generation < 0:
            raise ValueError(f"generation must be >= 0, got {self.generation}")
        parents = [p.strip() for p in self.parents if p and p.strip()]
        if self.expert_id in parents:
            raise ValueError(f"expert {self.expert_id} lists itself as a parent")
        if len(set(parents)) != len(parents):
            raise ValueError(f"expert {self.expert_id} has duplicate parents: {parents}")
        self.parents = parents

    @property
    def is_dynamic(self) -> bool:
        """True for an expert this system created, as opposed to a declared one."""
        return self.generation > 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExpertIdentity:
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"ExpertIdentity has unknown field(s): {unknown}")
        payload = dict(data)
        if isinstance(payload.get("status"), str):
            payload["status"] = ExpertLifecycleState(payload["status"])
        return cls(**payload)


@dataclass
class ExpertRecord:
    """An expert's identity plus its measured metrics and provenance."""

    identity: ExpertIdentity
    metrics: ExpertMetrics = field(default_factory=ExpertMetrics)
    lineage_reason: str = ""
    """Human-readable creation cause. Copied from the identity at creation and
    kept separately so a reason can be corrected without rewriting identity."""

    provenance: dict[str, Any] = field(default_factory=dict)
    """``{"source", "date", "agent", "task", "model", "experiences", "tests", "version"}``.

    A learned capability with no provenance is not trustworthy, so this is
    populated at creation and never invented later.
    """

    payload: dict[str, Any] = field(default_factory=dict)
    """Router-visible material (prompt, tool groups, routing slot). Empty is legal."""

    def transition(self, target: ExpertLifecycleState) -> ExpertLifecycleState:
        """Move to ``target``, refusing any edge not in the state machine.

        Raises ``ValueError`` naming both states and the legal targets. An
        illegal transition is a caller bug, and silently accepting one is how a
        trial expert becomes a permanent one without passing a gate.
        """
        current = self.identity.status
        if target is current:
            return current
        legal = _LEGAL_TRANSITIONS.get(current, frozenset())
        if target not in legal:
            allowed = ", ".join(sorted(s.value for s in legal)) or "<terminal>"
            raise ValueError(f"expert {self.identity.expert_id}: illegal transition {current.value} -> {target.value}. Legal from {current.value}: {allowed}.")
        self.identity.status = target
        return target

    @property
    def is_routable(self) -> bool:
        """Whether the router may select this expert for production work."""
        return self.identity.status is ExpertLifecycleState.ACTIVE

    @property
    def is_trial(self) -> bool:
        return self.identity.status is ExpertLifecycleState.TRIAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "metrics": self.metrics.to_dict(),
            "lineage_reason": self.lineage_reason,
            "provenance": dict(self.provenance),
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExpertRecord:
        if not isinstance(data, dict):
            raise ValueError(f"ExpertRecord payload must be an object, got {type(data).__name__}")
        for key in ("identity", "metrics"):
            if key not in data:
                raise ValueError(f"ExpertRecord payload missing required field {key!r}")
        known = {"identity", "metrics", "lineage_reason", "provenance", "payload"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"ExpertRecord has unknown field(s): {unknown}")
        metrics_raw = data["metrics"]
        if not isinstance(metrics_raw, dict):
            raise ValueError("ExpertRecord.metrics must be an object")
        return cls(
            identity=ExpertIdentity.from_dict(data["identity"]),
            metrics=ExpertMetrics.from_dict(metrics_raw),
            lineage_reason=str(data.get("lineage_reason", "")),
            provenance=dict(data.get("provenance") or {}),
            payload=dict(data.get("payload") or {}),
        )


@dataclass
class ExperienceTelemetry:
    """Optional execution telemetry attached to an experience record.

    **Every field defaults, and every field is optional.** An
    :class:`alpha.learning.experience.models.ExperienceRecord` written before
    this type existed round-trips unchanged with ``telemetry=None``, which is why
    this lives beside the existing record rather than reshaping it.

    The separation is deliberate. ``ExperienceRecord`` models *what was learned*
    (a lesson, a pitfall, a fact); this models *what ran* (which agent, which
    tool, how long, at what cost). A task with no telemetry is not a broken
    record — it is an older record, and the two questions are different.
    """

    agents: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None
    duration_measured: bool = False
    """Distinguishes "no duration recorded" from a measured ``0.0``."""

    #: Provenance roles. Multi-agent work records who *did what*, because a
    #: claim from an executor is not a claim from a critic.
    proposed_by: str = ""
    executed_by: str = ""
    verified_by: str = ""
    criticized_by: str = ""
    human_confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperienceTelemetry:
        if not isinstance(data, dict):
            raise ValueError(f"ExperienceTelemetry must be an object, got {type(data).__name__}")
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"ExperienceTelemetry has unknown field(s): {unknown}")
        return cls(**dict(data))

    def role_ledger(self) -> dict[str, str]:
        """Only the roles that were actually filled in.

        An absent role is omitted rather than reported as ``""`` — "nobody
        verified this" and "someone named the empty string" are different
        facts, and the first one must stay visible.
        """
        out: dict[str, str] = {}
        for key in ("proposed_by", "executed_by", "verified_by", "criticized_by"):
            value = getattr(self, key)
            if value:
                out[key.replace("_by", "")] = value
        return out


@dataclass
class LearningEvent:
    """One auditable learning event (prompt §34). Written to the journal always."""

    event_id: str = field(default_factory=lambda: f"learn_{uuid.uuid4().hex[:12]}")
    kind: str = ""
    """``replay_sample`` | ``expert_grown`` | ``expert_promoted`` | ``expert_rejected``
    | ``expert_pruned`` | ``plasticity_shift`` | ``snapshot`` | ``rollback`` | ...

    Free-form on purpose: refusing an unrecognised kind would make adding a
    learning action a schema migration, and the journal's job is to record what
    happened, not to constrain it to a closed set.
    """

    mode: LearningMode = LearningMode.OBSERVE_ONLY
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    regression: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)
    decision: str = ""
    """``PROMOTE`` | ``KEEP_TRIAL`` | ``REJECT`` | ``ROLLBACK`` | ``NO_CHANGE``."""

    reason: str = ""
    experiences: list[str] = field(default_factory=list)
    expert_id: str = ""
    experiment_id: str = ""
    recorded_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mode"] = self.mode.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearningEvent:
        if not isinstance(data, dict):
            raise ValueError(f"LearningEvent payload must be an object, got {type(data).__name__}")
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"LearningEvent has unknown field(s): {unknown}")
        payload = dict(data)
        if isinstance(payload.get("mode"), str):
            payload["mode"] = LearningMode(payload["mode"])
        return cls(**payload)
