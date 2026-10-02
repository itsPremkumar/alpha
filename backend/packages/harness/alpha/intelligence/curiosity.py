"""Durable, degeneracy-guarded curiosity as a learning signal.

What already existed, and what was wrong with it
--------------------------------------------------
:mod:`alpha.agency.curiosity` ships a ``CuriosityScorer`` computing novelty and
prediction error. Two measured problems stopped it from being useful:

1. **It has zero production callers.** It is exported and imported by
   ``agency_competence_tool`` but nothing ever constructs it, so it is a
   capability in name only.
2. **Its novelty map is a plain in-memory dict.** After a restart every
   situation is unfamiliar again, so novelty pins to 1.0 uniformly — which is
   the *opposite* of a novelty signal. A scorer that says "everything is new"
   after every restart carries no information.

This module keeps the existing scorer's shape and fixes both, adding the third
intrinsic signal the literature names and the guard the existing one lacks.

Three intrinsic signals
-----------------------
The survey's formulation is intrinsic value from **high prediction error** or
**verifier disagreement**. All three are here:

* :attr:`CuriositySignals.novelty` — durable, and it survives restart.
* :attr:`CuriositySignals.prediction_error` — predicted vs observed outcome.
* :attr:`CuriositySignals.disagreement` — how much independent verifiers
  disagreed. This is the one the existing scorer cannot express, and it is the
  most valuable: **disagreement is information the system already produced and
  threw away.** A task three verifiers graded differently is worth investigating
  for a reason novelty cannot supply.

The degeneracy guard is the load-bearing part
---------------------------------------------
The literature explicitly warns that intrinsic reward "can fall into a
degenerate" exploration regime. The concrete failure: a pure novelty bonus
rewards the agent for doing the *strangest available thing*, and strange is not
the same as informative.

So :meth:`CuriosityLedger.rank` refuses a target whose curiosity is high but
whose **competence evidence is zero**. Novelty plus no evidence of competence is
the signature of an agent about to waste a large budget on something it cannot
even attempt. Such a target is reported as
:attr:`DegeneracyVerdict.EXPLORATION_WITHOUT_COMPETENCE`, which is a *refusal to
explore*, and it is deliberately the opposite of what the raw score would say.

Bounded importance
------------------
Curiosity contributes a **capped** bonus. It can raise a target's rank; it can
never make an unexplored, incompetent target outrank a proven one, which is the
structural guarantee that keeps Phase C's behaviour-collapse gate meaningful.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.persistence.storekit.atomic import atomic_write_json

logger = logging.getLogger(__name__)

__all__ = [
    "CURIOSITY_RELATIVE_PATH",
    "CURIOSITY_SCHEMA_VERSION",
    "CuriositySignals",
    "DegeneracyVerdict",
    "CuriosityTarget",
    "CuriosityRanking",
    "CuriosityLedger",
    "DEFAULT_CURIOSITY_CAP",
    "curiosity_ledger",
]

CURIOSITY_RELATIVE_PATH = Path("intelligence") / "curiosity.json"
CURIOSITY_SCHEMA_VERSION = 1

#: Hard ceiling on what curiosity can contribute to any rank. Small on purpose:
#: curiosity is a tiebreaker between plausible targets, never a reason to attempt
#: something the agent has no evidence it can do.
DEFAULT_CURIOSITY_CAP = 0.15


def _now() -> float:
    return time.time()


def situation_digest(situation: Any) -> str:
    """Stable digest of a situation, so familiarity is keyed by content.

    Content-keyed rather than call-site-keyed: what matters for novelty is
    whether Alpha has *seen this kind of situation*, not where it was called
    from. A call-site key would make every call site novel exactly once, which
    is novelty of the code rather than of the world.
    """
    encoded = json.dumps(situation, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CuriositySignals:
    """The three intrinsic signals. Each is optional; absent means unmeasured."""

    novelty: float = 0.0
    prediction_error: float | None = None
    disagreement: float | None = None

    def __post_init__(self) -> None:
        for name in ("novelty", "prediction_error", "disagreement"):
            value = getattr(self, name)
            if value is None:
                continue
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be within [0.0, 1.0], got {value!r}")

    @property
    def composite(self) -> float:
        """Weighted intrinsic value in ``[0, 1]``.

        Disagreement is weighted highest because it is the only signal that
        reflects the *system's own uncertainty* rather than the agent's
        unfamiliarity with a situation. Unmeasured signals are excluded and the
        mean renormalised, exactly as :mod:`alpha.intelligence.difficulty` does —
        defaulting an absent signal to zero would make "nobody disagreed" and
        "nobody checked" identical.
        """
        parts: list[tuple[float, float]] = [(self.novelty, 0.4)]
        if self.prediction_error is not None:
            parts.append((self.prediction_error, 0.35))
        if self.disagreement is not None:
            parts.append((self.disagreement, 0.25))
        total_weight = sum(weight for _value, weight in parts)
        if total_weight <= 0:
            return 0.0
        return min(1.0, sum(value * weight for value, weight in parts) / total_weight)

    def to_dict(self) -> dict[str, Any]:
        return {
            "novelty": round(self.novelty, 6),
            "prediction_error": self.prediction_error,
            "disagreement": self.disagreement,
            "composite": round(self.composite, 6),
        }


class DegeneracyVerdict(StrEnum):
    """Why a target was or was not worth exploring."""

    OK = "OK"
    EXPLORATION_WITHOUT_COMPETENCE = "EXPLORATION_WITHOUT_COMPETENCE"
    """High curiosity, zero competence evidence. **A refusal to explore.**"""

    ALREADY_FAMILIAR = "ALREADY_FAMILIAR"
    LOW_SIGNAL = "LOW_SIGNAL"


@dataclass(frozen=True)
class CuriosityTarget:
    """One candidate to explore, with its signals and its competence evidence."""

    key: str
    signals: CuriositySignals
    competence: float = 0.0
    """0..1. Evidence Alpha can already *do* this. Not a score — a floor check."""

    attempts: int = 0

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("key must be a non-empty string")
        if not 0.0 <= self.competence <= 1.0:
            raise ValueError(f"competence must be within [0.0, 1.0], got {self.competence!r}")

    @property
    def verdict(self) -> DegeneracyVerdict:
        composite = self.signals.composite
        if self.attempts > 0 and self.competence <= 0.0:
            # It has been tried and has no recorded competence: that is failure,
            # not curiosity.
            return DegeneracyVerdict.ALREADY_FAMILIAR
        if composite >= 0.5 and self.competence <= 0.0 and self.attempts == 0:
            return DegeneracyVerdict.EXPLORATION_WITHOUT_COMPETENCE
        if composite < 0.2:
            return DegeneracyVerdict.LOW_SIGNAL
        return DegeneracyVerdict.OK

    def bonus(self, *, cap: float = DEFAULT_CURIOSITY_CAP) -> float:
        """Capped rank contribution. Zero unless the verdict is OK."""
        if self.verdict is not DegeneracyVerdict.OK:
            return 0.0
        return cap * self.signals.composite

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "signals": self.signals.to_dict(),
            "competence": round(self.competence, 6),
            "attempts": self.attempts,
            "verdict": self.verdict.value,
            "bonus": round(self.bonus(), 6),
        }


@dataclass(frozen=True)
class CuriosityRanking:
    """Ranked targets with the refusals kept visible."""

    ranked: tuple[CuriosityTarget, ...]
    refused: tuple[tuple[CuriosityTarget, DegeneracyVerdict], ...]
    cap: float

    @property
    def keys(self) -> list[str]:
        return [target.key for target in self.ranked]

    @property
    def refused_keys(self) -> list[str]:
        return [target.key for target, _verdict in self.refused]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranked": [target.to_dict() for target in self.ranked],
            "refused": [{"key": target.key, "verdict": verdict.value} for target, verdict in self.refused],
            "cap": round(self.cap, 6),
        }


class CuriosityLedger:
    """Durable familiarity counts plus the ranking policy.

    Durability is the fix for the restart bug: ``familiarity`` is persisted, so
    novelty does not reset to 1.0 on every process start. Counts saturate at
    ``saturation`` so the map cannot grow without bound.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        saturation: float = 1.0,
        familiarity_step: float = 0.2,
        cap: float = DEFAULT_CURIOSITY_CAP,
    ) -> None:
        if saturation <= 0:
            raise ValueError(f"saturation must be > 0, got {saturation}")
        if not 0 < familiarity_step <= saturation:
            raise ValueError(f"familiarity_step must be within (0, saturation], got {familiarity_step}")
        if cap < 0:
            raise ValueError(f"cap must be >= 0, got {cap}")
        self.path = Path(path) if path is not None else runtime_home() / CURIOSITY_RELATIVE_PATH
        self.saturation = float(saturation)
        self.familiarity_step = float(familiarity_step)
        self.cap = float(cap)
        self._lock = threading.RLock()
        self._familiarity: dict[str, float] = {}
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A corrupt familiarity map degrades to empty rather than failing the
            # caller's request: novelty will over-report, which is a safe
            # direction (explore more), and the log says so.
            logger.warning("curiosity ledger %s is unreadable (%s); starting empty, so novelty will over-report", self.path, exc)
            return
        if not isinstance(payload, dict) or payload.get("schema_version") != CURIOSITY_SCHEMA_VERSION:
            logger.warning("curiosity ledger %s has an unrecognised schema; starting empty", self.path)
            return
        familiarity = payload.get("familiarity")
        if isinstance(familiarity, dict):
            self._familiarity = {str(key): float(value) for key, value in familiarity.items() if isinstance(value, (int, float)) and not isinstance(value, bool)}

    def _save(self) -> None:
        try:
            atomic_write_json(
                self.path,
                {"schema_version": CURIOSITY_SCHEMA_VERSION, "familiarity": dict(self._familiarity), "updated_at": _now()},
            )
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("could not persist curiosity ledger %s: %s", self.path, exc)

    # -- familiarity --------------------------------------------------------

    def novelty(self, situation: Any) -> float:
        """Novelty of a situation in ``[0, 1]``, durable across restarts."""
        digest = situation_digest(situation)
        with self._lock:
            familiarity = self._familiarity.get(digest, 0.0)
        return max(0.0, 1.0 - familiarity)

    def observe(self, situation: Any) -> float:
        """Record an encounter and return the resulting novelty.

        This is the write path the existing in-memory scorer lacked. Returns
        novelty *after* the increment, so a caller can log "we had already seen
        this" without a second lookup.
        """
        digest = situation_digest(situation)
        with self._lock:
            familiarity = min(self.saturation, self._familiarity.get(digest, 0.0) + self.familiarity_step)
            self._familiarity[digest] = familiarity
            self._save()
        return max(0.0, 1.0 - familiarity)

    def forget(self, situation: Any) -> bool:
        """Drop one familiarity entry. Returns whether it existed."""
        digest = situation_digest(situation)
        with self._lock:
            existed = self._familiarity.pop(digest, None) is not None
            if existed:
                self._save()
        return existed

    def familiarity_size(self) -> int:
        with self._lock:
            return len(self._familiarity)

    # -- ranking ------------------------------------------------------------

    def rank(self, targets: Iterable[CuriosityTarget]) -> CuriosityRanking:
        """Rank targets by intrinsic value, refusing degenerate exploration.

        Ordering is deterministic: bonus descending, then competence descending,
        then key. The competence tiebreaker is deliberate — when two targets have
        equal curiosity, the one Alpha can demonstrably *do* comes first, which
        is the opposite of what a pure novelty ranking would produce.
        """
        ranked: list[CuriosityTarget] = []
        refused: list[tuple[CuriosityTarget, DegeneracyVerdict]] = []
        for target in targets:
            verdict = target.verdict
            if verdict is DegeneracyVerdict.OK:
                ranked.append(target)
            else:
                refused.append((target, verdict))
        ranked.sort(key=lambda item: (-item.bonus(), -item.competence, item.key))
        return CuriosityRanking(ranked=tuple(ranked), refused=tuple(refused), cap=self.cap)

    # -- signals ------------------------------------------------------------

    def signals_for(
        self,
        situation: Any,
        *,
        predicted_outcome: float | None = None,
        actual_outcome: float | None = None,
        disagreement: float | None = None,
    ) -> CuriositySignals:
        """Assemble signals for a situation, keeping unmeasured terms ``None``."""
        error: float | None = None
        if predicted_outcome is not None and actual_outcome is not None:
            error = min(1.0, abs(float(predicted_outcome) - float(actual_outcome)))
        return CuriositySignals(
            novelty=self.novelty(situation),
            prediction_error=error,
            disagreement=disagreement,
        )

    def target_for(
        self,
        situation: Any,
        *,
        competence: float = 0.0,
        attempts: int = 0,
        disagreement: float | None = None,
    ) -> CuriosityTarget:
        """Build a target with novelty resolved against the durable map."""
        return CuriosityTarget(
            key=situation_digest(situation),
            signals=self.signals_for(situation, disagreement=disagreement),
            competence=competence,
            attempts=attempts,
        )

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": CURIOSITY_SCHEMA_VERSION,
                "familiarity_size": len(self._familiarity),
                "saturation": self.saturation,
                "cap": self.cap,
                "path": str(self.path),
                "top_familiar": sorted(self._familiarity.items(), key=lambda item: -item[1])[:10],
            }


_LEDGER_LOCK = threading.Lock()
_LEDGER: CuriosityLedger | None = None


def curiosity_ledger() -> CuriosityLedger:
    """Process-wide ledger. Configured from the ``intelligence`` section on first use."""
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            from alpha.intelligence.config import intelligence_config

            _LEDGER = CuriosityLedger(cap=intelligence_config().curiosity_cap)
        return _LEDGER
