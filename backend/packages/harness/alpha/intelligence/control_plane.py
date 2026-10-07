"""The intelligence control plane: one composed answer to "Is Alpha actually becoming more capable?"

Why this module exists
----------------------
``alpha.intelligence`` Phases A–H each measure one property of the learning loop,
and ``self_knowledge`` projects each subsystem separately — but nothing composed
them into a single operational status an operator could read in one bounded
call. This is that composition (the P0 slice of the mini-AGI / volotat lessons:
one control plane over the engines that already exist).

It is a **composition, never a seventh source of truth**: every figure below is
read from the subsystem that already owns it, and nothing here recomputes a
number somebody else measures. Three rules decide the payload:

1. **A source that could not be read is not zero.** Each section arrives as
   ``{"available": false, "reason": "<real error>"}``, and every metric derived
   from it is ``basis: "unavailable"`` with ``value: None`` — "I could not look"
   and "I looked and found nothing" lead to opposite decisions.
2. **An unmeasured metric stays unmeasured.** The P14 dashboard figures with no
   aggregate owner (``mission_success_rate``, ``cost_per_success``, the rest in
   :data:`UNOWNED_METRICS`) are declared ``basis: "unowned"`` with the owning
   subsystem named in ``source`` — never defaulted to ``0``, ``0.0`` or
   ``false``.
3. **There is one loop-health composition, not two.**
   :func:`build_loop_health_report` is called by both
   ``GET /api/intelligence/health`` and :func:`build_control_plane`, so the two
   surfaces cannot grow two opinions about the same state.

Read-only, like every route in the intelligence router: this module performs no
writes, registers no background work, and its summary cannot mutate anything.

Tests: ``backend/tests/test_intelligence_control_plane.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "SCHEMA_VERSION",
    "SOURCE_SECTIONS",
    "UNOWNED_METRICS",
    "VALID_BASIS",
    "MetricReading",
    "UnownedMetric",
    "build_control_plane",
    "build_loop_health_report",
]

#: Payload contract shared with the frontend types and the tests. Bump only for
#: a breaking shape change — both pin this exact string.
SCHEMA_VERSION = "alpha.control-plane.v1"

#: The four honest answers a metric can have. ``measured`` is the only one that
#: carries a value; every other basis serialises ``value: null`` with a reason.
VALID_BASIS = frozenset({"measured", "unmeasured", "unavailable", "unowned"})

#: Sections that each read exactly one subsystem, in payload order. The summary
#: names the unavailable ones by these keys (``sources_unavailable``).
SOURCE_SECTIONS = (
    "mode",
    "loop_health",
    "ledger",
    "journal",
    "capability_fabric",
    "replay",
    "goals",
)


@dataclass(frozen=True)
class UnownedMetric:
    """A dashboard figure that has no aggregate owner yet.

    Declared rather than dropped: the P14 health dashboard asks for these, and
    an absent metric reads as "not tracked" while a zero reads as "measured at
    zero" — the exact confusion the honesty contract exists to prevent. ``source``
    names the subsystem that *would* own the number, so wiring it later is a
    local change instead of an archaeological one.
    """

    name: str
    unit: str
    reason: str
    source: str


#: The P14 metrics nobody computes today, with the reason each is honestly
#: absent. Every entry must carry a non-empty ``reason`` and ``source`` —
#: ``test_the_registry_itself_is_internally_consistent`` pins that.
UNOWNED_METRICS: tuple[UnownedMetric, ...] = (
    UnownedMetric(
        name="mission_success_rate",
        unit="ratio",
        reason="no subsystem aggregates mission outcomes into a rate; missions are recorded per execution",
        source="alpha.mission",
    ),
    UnownedMetric(
        name="verified_completion_rate",
        unit="ratio",
        reason="acceptance evidence is evaluated per run and never aggregated into a completion rate",
        source="alpha.runtime.runs.verification",
    ),
    UnownedMetric(
        name="unverified_completion_rate",
        unit="ratio",
        reason="terminal run states are recorded per run, not split into verified/unverified in aggregate",
        source="alpha.runtime.runs",
    ),
    UnownedMetric(
        name="recovery_success_rate",
        unit="ratio",
        reason="recovery decisions are made per failure; no owner counts attempts against outcomes",
        source="alpha.recovery.policies",
    ),
    UnownedMetric(
        name="cost_per_success",
        unit="currency",
        reason="cost is priced per model and success is judged per run; no owner pairs the two",
        source="alpha.models.pricing",
    ),
    UnownedMetric(
        name="learning_gain_per_replay",
        unit="score_delta",
        reason="the reservoir orders items for replay but never measures before/after gain from one",
        source="alpha.intelligence.replay",
    ),
    UnownedMetric(
        name="regression_rate",
        unit="ratio",
        reason="the standing suite reports coverage, not executed comparisons counted over time",
        source="alpha.intelligence.regression",
    ),
    UnownedMetric(
        name="model_availability",
        unit="ratio",
        reason="models are declared in config, not probed; availability would need a live health check that is not run",
        source="alpha.models",
    ),
    UnownedMetric(
        name="memory_health",
        unit="state",
        reason="memory stores answer per subsystem; no owner probes them for a single health figure",
        source="alpha.memory",
    ),
    UnownedMetric(
        name="swarm_health",
        unit="state",
        reason="swarm telemetry is per plan; no cross-plan health aggregate is computed",
        source="alpha.swarm",
    ),
    UnownedMetric(
        name="stalled_runs",
        unit="count",
        reason="runs record their own status; no sweep counts runs that stopped making progress",
        source="alpha.runtime.runs",
    ),
)


@dataclass
class MetricReading:
    """One figure with its measurement basis.

    ``value`` is normalised to ``None`` for any basis other than ``measured``
    **at construction**, so a caller cannot smuggle a raw ``0`` through as an
    ``unowned`` or ``unavailable`` reading by forgetting to clear it: the
    dataclass makes the dishonest shape unrepresentable rather than merely
    discouraged.
    """

    name: str
    value: Any
    unit: str
    basis: str
    source: str
    reason: str = ""

    def __post_init__(self) -> None:
        if self.basis not in VALID_BASIS:
            raise ValueError(f"metric {self.name!r} declares basis {self.basis!r}; expected one of {sorted(VALID_BASIS)}")
        if self.basis != "measured":
            self.value = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "basis": self.basis,
            "reason": self.reason,
            "source": self.source,
        }


def build_loop_health_report() -> dict[str, Any]:
    """Compose Phases A–E into the loop-health answer.

    This is the **single** composition: ``GET /api/intelligence/health`` and
    :func:`build_control_plane` both call it, so the two surfaces cannot drift
    into two opinions about the same state.

    Synchronous on purpose: callers run it through ``asyncio.to_thread`` (it
    parses config and scans the journal), so it must not itself be a coroutine.
    A config that will not parse surfaces as a disclosed ``insufficient_data``
    verdict rather than an exception that hides the health endpoint entirely —
    and that fallback deliberately omits ``scored_attempts``, because no
    assessment ran; a caller can tell "measured zero attempts" from "could not
    measure at all" by that key.

    ``regime: "insufficient_data"`` is a first-class answer. A loop with no
    scored attempts has not been measured, and reporting ``"stable"`` there
    would be fabricated reassurance.
    """
    # Function-local imports so tests (and reality) patch the module attribute
    # the caller named, not a binding frozen at *this* module's import time.
    from alpha.intelligence.config import intelligence_config
    from alpha.intelligence.evaluator_stability import NoiseFloor
    from alpha.intelligence.evidence_ledger import Subsystem, get_evidence_ledger
    from alpha.intelligence.journal import LearningJournal
    from alpha.intelligence.loop_health import SaturationDetector, assess_loop_health

    try:
        config = intelligence_config()
    except Exception as exc:  # noqa: BLE001 - the disclosure IS the answer
        return {
            "report": {
                "regime": "insufficient_data",
                "reason": f"loop health could not be computed: {type(exc).__name__}: {exc}",
            },
            "required_subsystems": [],
            "ledger": {},
            "observed_events": 0,
        }

    ledger = get_evidence_ledger()
    ledger.configure([Subsystem(name) for name in config.required_subsystems])
    journal = LearningJournal()
    entries, _corrupt = journal.recent(
        kinds=("loop_observed", "pathway_verified", "noise_measured", "diversity_compared"),
        limit=config.regression.max_retries * 4,
    )
    detector = SaturationDetector()
    for entry in entries:
        after = entry.event.after
        if not isinstance(after, dict) or "improved" not in after:
            continue
        detector.observe(bool(after.get("improved")), gain=after.get("gain"))
    report = assess_loop_health(
        detector=detector,
        noise=NoiseFloor(
            metric="score",
            floor=0.0,
            samples=0,
            observed=False,
            reason="no stability probe has been run in this process yet; run measure_noise_floor() before trusting any delta",
            source="unmeasured",
        ),
        convergence=None,
        noise_floor_source=config.regression.noise_floor_source,
    )
    return {
        "report": report.to_dict(),
        "required_subsystems": list(config.required_subsystems),
        "ledger": ledger.stats(),
        "observed_events": len(entries),
    }


# ---------------------------------------------------------------------------
# Section readers — one subsystem each, never a second projection
# ---------------------------------------------------------------------------


def _envelope(read: Callable[[], Any]) -> dict[str, Any]:
    """Normalise any read into the availability envelope.

    ``read`` may return an envelope already (the ``self_knowledge``
    projections do) or raise (a raw subsystem read); either way the caller
    gets exactly ``{"available", "reason", "data"}``. One dead source thus
    discloses itself instead of hiding every other answer behind a 500.
    """
    try:
        result = read()
    except Exception as exc:  # noqa: BLE001 - the disclosure IS the answer
        logger.info("control-plane source unavailable: %s", exc)
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "data": None}
    if isinstance(result, dict) and "available" in result and "reason" in result:
        return {
            "available": bool(result["available"]),
            "reason": str(result.get("reason") or ""),
            "data": result.get("data"),
        }
    return {"available": True, "reason": "", "data": result}


def _read_mode() -> Any:
    """Effective learning mode, through the existing self-knowledge projection."""
    from alpha.intelligence.self_knowledge import get_self_knowledge

    return get_self_knowledge().mode()


def _read_loop_health() -> dict[str, Any]:
    """The one loop-health composition; may raise, and the envelope discloses."""
    return build_loop_health_report()


def _read_ledger() -> dict[str, Any]:
    """Phase E verdict counts. Loop health configures the quorum first."""
    from alpha.intelligence.evidence_ledger import get_evidence_ledger

    return get_evidence_ledger().stats()


def _read_journal() -> Any:
    """Journal tail plus chain integrity, through the existing projection."""
    from alpha.intelligence.self_knowledge import get_self_knowledge

    return get_self_knowledge().journal(limit=10)


def _read_capability_fabric() -> dict[str, Any]:
    """Non-terminal learned experts, straight off the fabric."""
    from alpha.intelligence.expert_fabric import get_expert_fabric

    fabric = get_expert_fabric()
    records = fabric.list(include_terminal=False)
    return {
        "active": len(records),
        "status_counts": fabric.status_counts(),
        "note": "active counts non-terminal learned experts; pruned records are excluded",
    }


def _read_replay() -> Any:
    """Reservoir occupancy, through the existing self-knowledge projection."""
    from alpha.intelligence.self_knowledge import get_self_knowledge

    return get_self_knowledge().replay()


def _read_goals() -> dict[str, Any]:
    """Tracked autonomous goals, read from a freshly constructed store.

    The store is built per read (the same path ``get_goal_store()`` resolves)
    so the count reflects disk now rather than a process-start snapshot. A
    degraded store is refused *before* ``list_goals()``: enumerating a store
    that failed to parse would answer "0 goals" for state that could not be
    read — the exact failure the ``load_error`` disclosure exists to prevent.
    """
    from alpha.harness.continuous.store import GoalStore

    store = GoalStore()
    if store.is_degraded:
        raise RuntimeError(f"goal store degraded, not enumerated: {store.load_error}")
    return {"tracked": len(store.list_goals()), "note": "counted from a freshly read store; a degraded store discloses instead"}


# ---------------------------------------------------------------------------
# Metrics and summary
# ---------------------------------------------------------------------------


def _metric(
    name: str,
    unit: str,
    source: str,
    section: dict[str, Any],
    extract: Callable[[Any], Any],
    *,
    unmeasured_reason: str = "",
) -> MetricReading:
    """Derive one metric from one section, honestly.

    A section that could not be read makes its metrics ``unavailable`` (the
    source failed); a section that answered *without* the field makes them
    ``unmeasured`` (the source had no figure). The two claims lead to opposite
    decisions, so they never collapse into one another, and neither ever
    becomes a number.
    """
    if not section["available"]:
        return MetricReading(name=name, value=None, unit=unit, basis="unavailable", source=source, reason=str(section["reason"]))
    try:
        value = extract(section["data"])
    except Exception as exc:  # noqa: BLE001 - absence must surface as a basis, never a raise
        return MetricReading(
            name=name,
            value=None,
            unit=unit,
            basis="unmeasured",
            source=source,
            reason=unmeasured_reason or f"{type(exc).__name__}: {exc}",
        )
    if value is None:
        return MetricReading(
            name=name,
            value=None,
            unit=unit,
            basis="unmeasured",
            source=source,
            reason=unmeasured_reason or "the source reported no value for this field",
        )
    return MetricReading(name=name, value=value, unit=unit, basis="measured", source=source)


def _build_metrics(sections: dict[str, dict[str, Any]]) -> list[MetricReading]:
    """Every metric the plane can honestly answer, in a stable order."""
    loop_data = sections["loop_health"]["data"] if sections["loop_health"]["available"] else None
    loop_report: dict[str, Any] = {}
    if isinstance(loop_data, dict) and isinstance(loop_data.get("report"), dict):
        loop_report = loop_data["report"]

    metrics: list[MetricReading] = [
        _metric("learning_enabled", "boolean", "alpha.intelligence.config", sections["mode"], lambda data: data["enabled"]),
        _metric(
            "loop_scored_attempts",
            "count",
            "alpha.intelligence.loop_health",
            sections["loop_health"],
            lambda data: data["report"]["scored_attempts"],
            unmeasured_reason=str(loop_report.get("reason") or "") or "the loop-health composition recorded no scored attempts",
        ),
        _metric("ledger_candidates", "count", "alpha.intelligence.evidence_ledger", sections["ledger"], lambda data: data["candidates"]),
        _metric("journal_chain_ok", "boolean", "alpha.intelligence.journal", sections["journal"], lambda data: data["integrity"]["ok"]),
        _metric(
            "journal_entries_checked",
            "count",
            "alpha.intelligence.journal",
            sections["journal"],
            lambda data: data["integrity"]["checked"],
        ),
        _metric("capability_active", "count", "alpha.intelligence.expert_fabric", sections["capability_fabric"], lambda data: data["active"]),
        _metric("replay_items", "count", "alpha.intelligence.replay", sections["replay"], lambda data: data["size"]),
        _metric("replay_utilisation", "ratio", "alpha.intelligence.replay", sections["replay"], lambda data: data["utilisation"]),
        _metric("goals_tracked", "count", "alpha.harness.continuous.store", sections["goals"], lambda data: data["tracked"]),
    ]
    metrics.extend(
        MetricReading(
            name=entry.name,
            value=None,
            unit=entry.unit,
            basis="unowned",
            source=entry.source,
            reason=entry.reason,
        )
        for entry in UNOWNED_METRICS
    )
    return metrics


def _build_summary(sections: dict[str, dict[str, Any]], metrics: list[MetricReading]) -> dict[str, Any]:
    """Counts per basis, the unavailable sources, and the one health state."""
    counts = {basis: 0 for basis in sorted(VALID_BASIS)}
    for reading in metrics:
        counts[reading.basis] += 1

    loop_section = sections["loop_health"]
    report: Any = loop_section["data"].get("report") if loop_section["available"] and isinstance(loop_section["data"], dict) else None
    # health_state mirrors the report verbatim; a composition that could not
    # run has no regime, and "unknown" is the honest answer — never "stable".
    health_state = report.get("regime", "unknown") if isinstance(report, dict) else "unknown"

    return {
        **counts,
        "total": len(metrics),
        "sources_unavailable": [name for name in SOURCE_SECTIONS if not sections[name]["available"]],
        "health_state": health_state,
    }


def build_control_plane() -> dict[str, Any]:
    """Compose every intelligence source into one read-only status payload.

    Synchronous on purpose: the Gateway route runs it via ``asyncio.to_thread``
    (it parses config, scans the journal and reads three JSON files), and tests
    call it directly. Every section is independently wrapped, so one dead
    source discloses itself while the rest still answer.
    """
    sections: dict[str, dict[str, Any]] = {
        # loop_health before ledger on purpose: the composition configures the
        # ledger's quorum as a side effect, and the ledger section reports the
        # stats of that same configured instance.
        "mode": _envelope(_read_mode),
        "loop_health": _envelope(_read_loop_health),
        "ledger": _envelope(_read_ledger),
        "journal": _envelope(_read_journal),
        "capability_fabric": _envelope(_read_capability_fabric),
        "replay": _envelope(_read_replay),
        "goals": _envelope(_read_goals),
    }
    metrics = _build_metrics(sections)
    return {
        "schema_version": SCHEMA_VERSION,
        **sections,
        "metrics": [reading.to_dict() for reading in metrics],
        "summary": _build_summary(sections, metrics),
    }
