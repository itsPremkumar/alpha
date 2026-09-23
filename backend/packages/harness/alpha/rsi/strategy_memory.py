"""RSI strategy memory: durable per-strategy statistics + reflection lessons (WP-D3, feature #23).

Honesty doctrine (ALPHA_RSI_IMPLEMENTATION_PLAN §5.6, binding):

* Every rate is computed **only** from recorded ledger events
  (``runtime_home()/rsi/lineage.jsonl`` once WP-A1 lands, plus the existing
  ``runtime_home()/evolution/ledger.jsonl``). Nothing here simulates,
  extrapolates, or invents outcomes.
* Zero attempts means ``promotion_rate``/``rollback_rate`` are ``None``
  ("unverified") — never ``0.0``/``1.0`` defaults that would read as
  measurements (see :func:`empty_stat` and :func:`stat_note`). If a consumer
  needs a scalar for an unverified stat, the doctrine requires a *disclosed*
  neutral ``0.5`` (see :func:`prior_for`).
* Rates are plain recorded-count ratios — no Laplace smoothing is applied or
  implied; :func:`stat_note` writes the formula and the concrete counts.
* Heuristic guidance from :func:`lesson_for` always carries ``evidence_kind``
  and a note; an unmatched failure returns ``{"status": "unresolved"}``
  instead of an invented root cause (spec §66 final paragraph).
* Persistence is atomic (temp file + :func:`os.replace`); a corrupt memory
  file loads as honest empty memory (``{}``), never fabricated stats.

Observe-only: this module has no promotion authority (plan §4, Phase D).

Adjudication A2: the real reflection support is
``alpha/evolution/retrospective_engine.py::analyze_recent_learnings``;
``alpha/reflection/resolvers.py`` is an unrelated import-path resolver and is
deliberately not referenced here.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.retrospective_engine import RetrospectiveEngine

logger = logging.getLogger(__name__)

#: Honest sentinels for events that carry no attribution. They label a real
#: gap ("this event did not say") rather than inventing a strategy/class.
UNATTRIBUTED_STRATEGY = "unattributed"
UNKNOWN_PROBLEM_CLASS = "unknown"

_RATE_DISCLOSURE = "plain recorded-count rates (no Laplace smoothing); counts come only from recorded ledger events"
_ZERO_ATTEMPT_DISCLOSURE = (
    "attempts=0 -> promotion_rate=None, rollback_rate=None (unverified: no recorded attempts). "
    "A consumer needing a scalar must use and disclose neutral 0.5, never a fabricated 0.0/1.0."
)

# Event parsing vocabulary. ``event``/``kind``/``action`` mirror the existing
# evolution ledger writer (evolution/engine.py) and tolerate A1's lineage
# schema adding aliases; unknown kinds are ignored, not guessed at.
_STRATEGY_KEYS = ("strategy", "operator", "surface")
_PROBLEM_KEYS = ("problem_class", "problem")
_KIND_KEYS = ("event", "kind", "action")
_UNIT_KEYS = ("candidate_id", "variant_id", "id")
_TS_KEYS = ("at", "timestamp", "ts", "updated_at")

_ATTEMPT_KINDS = frozenset({"proposed", "attempt", "started", "created", "generated", "candidate"})
_PROGRESS_KINDS = frozenset({"benchmarked", "gated", "evaluated", "queued"})
_PROMOTION_KINDS = frozenset({"promoted", "promotion"})
_REJECTION_KINDS = frozenset({"rejected", "rejection"})
_ROLLBACK_KINDS = frozenset({"rolled_back", "rollback"})
_RECOGNIZED_KINDS = _ATTEMPT_KINDS | _PROGRESS_KINDS | _PROMOTION_KINDS | _REJECTION_KINDS | _ROLLBACK_KINDS

# Mirrors the trigger_pattern strings RetrospectiveEngine.analyze_recent_learnings
# emits (evolution/retrospective_engine.py). The engine's generic
# "Proactive Quality Hardening" fallback is deliberately absent: it is not a
# root-cause explanation for any specific failure. A pattern-string drift
# degrades fail-closed to {"status": "unresolved"} — never to an invented lesson.
_PATTERN_FAMILY_TOKENS: dict[str, tuple[str, ...]] = {
    "Recurring Import / Module Resolution Failure": ("import", "modulenotfound"),
    "Runtime Type / Attribute Error": ("type", "attributeerror"),
}

_SYMPTOM_KEYS = ("symptom", "error", "error_summary", "message", "reason", "detail")

_MEMORY_VERSION = 1
_MEMORY: dict[str, StrategyStat] | None = None


@dataclass
class StrategyStat:
    """Durable per-(strategy, problem_class) counters rebuilt from ledger events.

    ``promotion_rate``/``rollback_rate`` are ``None`` (unverified) whenever
    ``attempts == 0``; consumers must never substitute 0.0/1.0 defaults.
    """

    strategy: str
    problem_class: str
    attempts: int
    promotions: int
    rollbacks: int
    rejections: int
    promotion_rate: float | None
    rollback_rate: float | None
    updated_at: float


@dataclass
class _Bucket:
    """Mutable accumulator for one (strategy, problem_class) bucket."""

    strategy: str
    problem_class: str
    attempts: int = 0
    promotions: int = 0
    rollbacks: int = 0
    rejections: int = 0
    updated_at: float = 0.0
    # unit id -> outcome tokens already counted for that unit, so one candidate
    # is one attempt and can contribute at most one promotion/rejection/rollback.
    outcomes: dict[str, set[str]] = field(default_factory=dict)


def memory_key(strategy: str, problem_class: str) -> str:
    """Compose the ``dict[str, StrategyStat]`` key for a (strategy, problem class) pair."""
    return f"{strategy}::{problem_class}"


def empty_stat(strategy: str, problem_class: str, *, updated_at: float = 0.0) -> StrategyStat:
    """Honest zero-attempt stat: ``attempts=0`` and both rates ``None`` ("unverified")."""
    return StrategyStat(
        strategy=strategy,
        problem_class=problem_class,
        attempts=0,
        promotions=0,
        rollbacks=0,
        rejections=0,
        promotion_rate=None,
        rollback_rate=None,
        updated_at=updated_at,
    )


def stat_note(stat: StrategyStat) -> str:
    """Disclosure note: formula + concrete counts behind this stat's rates."""
    if stat.attempts <= 0:
        return _ZERO_ATTEMPT_DISCLOSURE
    return (
        f"promotion_rate = promotions/attempts = {stat.promotions}/{stat.attempts} = {stat.promotion_rate}; "
        f"rollback_rate = rollbacks/attempts = {stat.rollbacks}/{stat.attempts} = {stat.rollback_rate}; "
        f"{_RATE_DISCLOSURE}"
    )


def _first_text(event: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_timestamp(event: dict) -> float | None:
    for key in _TS_KEYS:
        value = event.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        return float(value)
    return None


def rebuild_from_ledger(lines: Iterable[dict]) -> dict[str, StrategyStat]:
    """Pure rebuild of per-strategy stats from parsed ledger events.

    Counting rules (hand-computable, deterministic):

    * An event is counted only when its kind is recognized (proposed/attempt/
      started/created/generated/candidate, benchmarked/gated/evaluated/queued,
      promoted, rejected, rolled_back); unknown kinds and non-dict entries are
      ignored entirely — they create no phantom attempts.
    * One *unit* (``candidate_id``/``variant_id``/``id``; an id-less event is
      its own unit) under one (strategy, problem class) is **one attempt**,
      regardless of how many lifecycle events it emits, so rates stay in [0, 1].
    * Promotions, rollbacks, and rejections are counted distinctly per unit
      (a candidate can be promoted and later rolled back: both count), each at
      most once per unit.
    * Missing attribution uses the honest sentinels
      :data:`UNATTRIBUTED_STRATEGY` / :data:`UNKNOWN_PROBLEM_CLASS`.
    * ``promotion_rate``/``rollback_rate`` = count/attempts only when
      ``attempts > 0``; otherwise ``None`` (unverified).
    * ``updated_at`` = max recorded timestamp of counted events (0.0 when the
      events carry none) — no wall clock, so two rebuilds of the same input
      are identical.

    Returned dict keys are :func:`memory_key` strings.
    """
    buckets: dict[tuple[str, str], _Bucket] = {}
    anonymous_units = 0
    for raw in lines:
        if not isinstance(raw, dict):
            continue
        kind = _first_text(raw, _KIND_KEYS)
        if kind is None:
            continue
        kind = kind.lower()
        if kind not in _RECOGNIZED_KINDS:
            continue
        strategy = _first_text(raw, _STRATEGY_KEYS) or UNATTRIBUTED_STRATEGY
        problem_class = _first_text(raw, _PROBLEM_KEYS) or UNKNOWN_PROBLEM_CLASS
        unit = _first_text(raw, _UNIT_KEYS)
        if unit is None:
            anonymous_units += 1
            unit = f"__event_{anonymous_units}"
        pair = (strategy, problem_class)
        bucket = buckets.get(pair)
        if bucket is None:
            bucket = _Bucket(strategy=strategy, problem_class=problem_class)
            buckets[pair] = bucket
        first_sighting = unit not in bucket.outcomes
        outcomes = bucket.outcomes.setdefault(unit, set())
        if first_sighting:
            bucket.attempts += 1
        if kind in _PROMOTION_KINDS and "promoted" not in outcomes:
            outcomes.add("promoted")
            bucket.promotions += 1
        elif kind in _REJECTION_KINDS and "rejected" not in outcomes:
            outcomes.add("rejected")
            bucket.rejections += 1
        elif kind in _ROLLBACK_KINDS and "rolled_back" not in outcomes:
            outcomes.add("rolled_back")
            bucket.rollbacks += 1
        ts = _first_timestamp(raw)
        if ts is not None and ts > bucket.updated_at:
            bucket.updated_at = ts
    return {
        memory_key(bucket.strategy, bucket.problem_class): StrategyStat(
            strategy=bucket.strategy,
            problem_class=bucket.problem_class,
            attempts=bucket.attempts,
            promotions=bucket.promotions,
            rollbacks=bucket.rollbacks,
            rejections=bucket.rejections,
            promotion_rate=(bucket.promotions / bucket.attempts) if bucket.attempts > 0 else None,
            rollback_rate=(bucket.rollbacks / bucket.attempts) if bucket.attempts > 0 else None,
            updated_at=bucket.updated_at,
        )
        for bucket in buckets.values()
    }


def memory_path() -> Path:
    """Default persisted location: ``runtime_home()/rsi/strategy_memory.json``."""
    return runtime_home() / "rsi" / "strategy_memory.json"


def save_memory(stats: Mapping[str, StrategyStat], *, path: Path | str | None = None) -> Path:
    """Persist stats atomically (temp file in the same dir + ``os.replace``).

    Best-effort like the evolution ledger writer: an ``OSError`` is logged as a
    warning and reported by returning the target path without raising; the
    target file is never left half-written (the temp file either replaces it
    wholesale or is the only casualty).
    """
    target = Path(path) if path is not None else memory_path()
    payload = {"version": _MEMORY_VERSION, "stats": [asdict(stat) for stat in stats.values()]}
    tmp = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, target)
    except OSError as exc:
        logger.warning("Could not persist strategy memory to %s: %s", target, exc)
    return target


def _stat_from_entry(entry: object) -> StrategyStat:
    """Validate one persisted entry; raises on any schema or honesty violation."""
    if not isinstance(entry, dict):
        raise TypeError(f"strategy memory entry is not an object: {entry!r}")
    required = (
        "strategy",
        "problem_class",
        "attempts",
        "promotions",
        "rollbacks",
        "rejections",
        "promotion_rate",
        "rollback_rate",
        "updated_at",
    )
    missing = [key for key in required if key not in entry]
    if missing:
        raise KeyError(f"strategy memory entry missing keys: {missing}")
    strategy = entry["strategy"]
    problem_class = entry["problem_class"]
    if not isinstance(strategy, str) or not isinstance(problem_class, str):
        raise TypeError("strategy/problem_class must be strings")
    counts: dict[str, int] = {}
    for key in ("attempts", "promotions", "rollbacks", "rejections"):
        value = entry[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TypeError(f"{key} must be a non-negative int, got {value!r}")
        counts[key] = value
    rates: dict[str, float | None] = {}
    for key in ("promotion_rate", "rollback_rate"):
        value = entry[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise TypeError(f"{key} must be a number or None, got {value!r}")
        rates[key] = None if value is None else float(value)
    updated_at = entry["updated_at"]
    if isinstance(updated_at, bool) or not isinstance(updated_at, (int, float)):
        raise TypeError(f"updated_at must be a number, got {updated_at!r}")
    # Honesty invariants: a zero-attempt stat must carry None rates, and a
    # positive-attempt stat's rates must equal count/attempts within 1e-9 and
    # lie in [0, 1]. Anything else is a fabricated/corrupt record -> reject.
    attempts = counts["attempts"]
    for key, count in (("promotion_rate", counts["promotions"]), ("rollback_rate", counts["rollbacks"])):
        rate = rates[key]
        if attempts == 0:
            if rate is not None:
                raise ValueError(f"zero-attempt stat carries {key}={rate!r} (must be None/unverified)")
        else:
            expected = count / attempts
            if rate is None or abs(rate - expected) > 1e-9 or not (0.0 <= rate <= 1.0):
                raise ValueError(f"{key}={rate!r} does not match recorded counts {count}/{attempts}")
    return StrategyStat(
        strategy=strategy,
        problem_class=problem_class,
        attempts=attempts,
        promotions=counts["promotions"],
        rollbacks=counts["rollbacks"],
        rejections=counts["rejections"],
        promotion_rate=rates["promotion_rate"],
        rollback_rate=rates["rollback_rate"],
        updated_at=float(updated_at),
    )


def load_memory(*, path: Path | str | None = None) -> dict[str, StrategyStat]:
    """Load persisted stats into the in-process cache.

    Honest-failure: a missing file is honest empty memory (``{}``), and a
    corrupt, wrong-version, or honesty-violating file is logged as a warning
    and also loads as ``{}`` — never as fabricated stats. The cache is always
    updated, so subsequent :func:`prior_for` calls see the honest state.
    """
    global _MEMORY
    target = Path(path) if path is not None else memory_path()
    stats: dict[str, StrategyStat] = {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != _MEMORY_VERSION:
            raise ValueError(f"unexpected strategy memory payload/version: {payload!r:.200}")
        entries = payload["stats"]
        if not isinstance(entries, list):
            raise TypeError("strategy memory 'stats' must be a list")
        loaded: dict[str, StrategyStat] = {}
        for entry in entries:
            stat = _stat_from_entry(entry)
            loaded[memory_key(stat.strategy, stat.problem_class)] = stat
        stats = loaded
    except FileNotFoundError:
        stats = {}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        logger.warning("Strategy memory %s is corrupt; loading honest empty memory: %s", target, exc)
        stats = {}
    _MEMORY = stats
    return stats


def clear_memory_cache() -> None:
    """Drop the in-process cache (next :func:`prior_for` re-reads the file)."""
    global _MEMORY
    _MEMORY = None


def get_memory() -> dict[str, StrategyStat]:
    """Return in-process memory, lazily loading the persisted file once."""
    global _MEMORY
    if _MEMORY is None:
        load_memory()
    return _MEMORY if _MEMORY is not None else {}


def prior_for(strategy: str, problem_class: str, *, memory: Mapping[str, StrategyStat] | None = None) -> StrategyStat | None:
    """Disclosed heuristic prior over recorded per-strategy stats (WP-B2 consumption contract).

    Returns the recorded :class:`StrategyStat` for ``(strategy, problem_class)``
    from ``memory`` (or the in-process cache, lazily loaded from the persisted
    file via :func:`load_memory`), or ``None`` when nothing is recorded —
    ``None`` means *unverified*, never *bad*.

    Consumption contract for ``rsi/generator.py::generate()`` (plan §3 WP-D3):

    * this is a **heuristic prior** and must be disclosed as such in any note
      or evidence attached to an operator choice;
    * exploration is **never disabled** by this prior — at least one
      non-dominant operator must always be emitted;
    * ``None`` or ``stat.attempts == 0`` (rates ``None``) ⇒ unverified: a
      consumer needing a scalar uses the disclosed neutral 0.5, never a
      fabricated 0.0/1.0 default;
    * per-operator stats are only ever these recorded counts — see
      :func:`stat_note` for the formula + concrete counts disclosure.
    """
    source = memory if memory is not None else get_memory()
    return source.get(memory_key(strategy, problem_class))


def default_ledger_paths() -> tuple[Path, Path]:
    """The two recorded ledgers this module rebuilds from (WP-A1 lineage + existing evolution)."""
    home = runtime_home()
    return (home / "rsi" / "lineage.jsonl", home / "evolution" / "ledger.jsonl")


def read_ledger_events(paths: Iterable[Path | str]) -> list[dict]:
    """Parse JSONL ledger events; corrupt lines/files degrade to honest gaps.

    A missing file (e.g. WP-A1's ``lineage.jsonl`` before it exists) is
    skipped silently; an unreadable file or corrupt/partial line is skipped
    with a warning that states the count — never guessed at or repaired.
    """
    events: list[dict] = []
    for raw_path in paths:
        path = Path(raw_path)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Ledger %s is unreadable; skipping it (honest gap): %s", path, exc)
            continue
        skipped = 0
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if not isinstance(event, dict):
                skipped += 1
                continue
            events.append(event)
        if skipped:
            logger.warning("Skipped %d corrupt/partial line(s) in %s", skipped, path)
    return events


def refresh_memory() -> dict[str, StrategyStat]:
    """Rebuild from the on-disk ledgers, persist atomically, update the cache."""
    global _MEMORY
    stats = rebuild_from_ledger(read_ledger_events(default_ledger_paths()))
    save_memory(stats)
    _MEMORY = dict(stats)
    return stats


def _failure_symptom(failure: object) -> str:
    """Extract the recorded symptom text from a failure object ("" when absent)."""
    if isinstance(failure, str):
        return failure.strip()
    if isinstance(failure, dict):
        parts: list[str] = []
        for key in _SYMPTOM_KEYS:
            value = failure.get(key)
            if value is None:
                continue
            text = value if isinstance(value, str) else str(value)
            if text.strip():
                parts.append(text.strip())
        return "; ".join(parts)
    return str(failure).strip()


def lesson_for(failure: object, *, project_id: str = "default", retrospective: object | None = None) -> dict | None:
    """Wrap ``RetrospectiveEngine.analyze_recent_learnings`` patterns for one failure.

    Returns ``None`` only when there is no failure at all (``failure is None``).

    * Matched: the retrospective engine emitted a *specific* pattern proposal
      whose token family appears in the recorded failure symptom → the
      proposal's guidance is returned with ``evidence_kind="heuristic"`` and a
      note stating the pattern and the concrete proposal counts. It is
      heuristic guidance, not a measured root cause.
    * Unresolved: everything else — unknown symptom, only the engine's generic
      fallback proposal (never usable as a root cause), no proposals, or an
      engine error → ``{"status": "unresolved", "evidence_kind": "unverified",
      "note": ...}`` with **no** invented explanation (spec §66 final
      paragraph). Engine errors fail closed with the real error text in the
      note.

    ``queue_for_approval=False`` is always passed: strategy memory never
    writes to the human approval queue.
    """
    if failure is None:
        return None
    symptom = _failure_symptom(failure)
    if not symptom:
        return {
            "status": "unresolved",
            "evidence_kind": "unverified",
            "note": "failure carries no symptom text; no root cause claimed.",
        }
    engine = retrospective if retrospective is not None else RetrospectiveEngine(project_id)
    try:
        proposals = engine.analyze_recent_learnings(queue_for_approval=False)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("Retrospective analysis failed; reporting unresolved lesson: %s", exc)
        return {
            "status": "unresolved",
            "evidence_kind": "unverified",
            "note": f"RetrospectiveEngine.analyze_recent_learnings failed ({exc!r}); no root cause claimed.",
        }
    symptom_lower = symptom.lower()
    for proposal in proposals:
        tokens = _PATTERN_FAMILY_TOKENS.get(proposal.trigger_pattern)
        if not tokens or not any(token in symptom_lower for token in tokens):
            continue
        return {
            "status": "matched",
            "evidence_kind": "heuristic",
            "trigger_pattern": proposal.trigger_pattern,
            "heuristic_summary": proposal.heuristic_summary,
            "proposed_instruction": proposal.proposed_instruction,
            "target_prompt_section": proposal.target_prompt_section,
            "note": (
                f"RetrospectiveEngine pattern '{proposal.trigger_pattern}' matched the recorded failure "
                f"symptom (proposals_emitted={len(proposals)}, matched=1); heuristic guidance "
                f"(evidence_kind=heuristic), not a measured root cause."
            ),
        }
    return {
        "status": "unresolved",
        "evidence_kind": "unverified",
        "note": (
            f"No retrospective pattern matched the recorded failure symptom "
            f"(proposals_emitted={len(proposals)}, matched=0); unverified — no root cause claimed."
        ),
    }
