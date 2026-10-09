"""Durable RRSI round state: ``S*``, the incumbent measurement, and the round score series.

Algorithm 2 threads three quantities across rounds that no single candidate
owns:

* ``S*`` — the best evolve-set score observed **so far**, which the acceptance
  floor (``Ŝ' ≥ S* − δ``) is measured against. It is not the incumbent's own
  score: the floor exists to stop a *sequence* of individually-tolerable
  regressions from eroding the best result the search has reached, so a store
  that reset ``S*`` to the current harness each round would delete exactly the
  protection it exists to provide.
* ``(Ŝ_t, Ĉ_t)`` — the incumbent harness's own score and absolute cost, the
  inputs to ``ΔS`` and ``ΔC``.
* the per-round best score series ``Ŝ_1..Ŝ_t`` — the only thing
  :func:`alpha.rsi.rrsi.exploration.detect_stall` can be computed from.

Binding rules (house durability doctrine — `continuous/store.py`,
`continual/state.py`, `groups/claims.py`):

* **An unreadable store reports unknown, never a default.** :attr:`state` is
  ``None`` while :attr:`readable` is ``False``. Synthesising ``S* = 0.0``
  would make every candidate clear a floor measured against nothing, and
  synthesising the incumbent as equal to the candidate would make every
  ``ΔS`` read as ``0`` — both would be fabrications wearing the shape of a
  sensible default.
* **Absent is not corrupt.** A missing file is a first run: readable, fresh,
  no rounds recorded.
* **``save`` reports whether the write landed.** It never raises and never
  claims durability it did not achieve: an atomic replace plus ``fsync`` that
  fails returns ``ok=False`` with the real error, and the in-memory state
  stays what it was.
* **This store is single-process.** It is atomic and restart-recoverable for
  one Gateway; it is not a cross-process exactly-once store and must not be
  described as one.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)

__all__ = ["ROUND_STORE_FILE_NAME", "DurableWrite", "RoundState", "RrsiRoundStore", "round_store_path"]

ROUND_STORE_FILE_NAME = "rrsi_rounds.json"

#: Written into every file so a future schema change is detectable rather
#: than silently mis-parsed.
STORE_VERSION = 1

#: Hard ceiling on retained per-round scores. Stall detection only ever reads
#: the last ``stall_window + 1`` values, so an unbounded series would grow
#: without ever being consulted past that point.
MAX_ROUND_SCORES = 512


def round_store_path(root: Path | str | None = None) -> Path:
    """``runtime_home()/rsi/rrsi_rounds.json`` (``root`` overrides the base)."""
    base = Path(root) if root is not None else runtime_home()
    return base / "rsi" / ROUND_STORE_FILE_NAME


@dataclass(frozen=True)
class DurableWrite:
    """Whether a write actually reached the disk, and why not if it did not."""

    ok: bool
    error: str | None = None
    path: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class RoundState:
    """One durable snapshot of the RRSI search's cross-round state.

    :attr:`round` is the **1-based index of the last completed round** (``0``
    means none), and :attr:`next_round` is the index of the round to run next.
    ``round_scores`` is the best-score-per-round series the stall indicator
    reads, and it holds **only rounds that produced a measured score**: a round
    with no measurement still advances ``round`` (it happened) but contributes
    no point, because a missing score is not a zero and cannot move a stall
    window.

    ``S*`` only ever rises (``S* ← max(S*, Ŝ')``), and stays ``None`` until a
    score exists — never ``0.0``, which would make the floor ``−δ`` and admit
    every candidate.
    """

    round: int = 0
    best_score: float | None = None
    best_candidate_id: str | None = None
    incumbent_score: float | None = None
    incumbent_cost: float | None = None
    incumbent_candidate_id: str | None = None
    round_scores: tuple[float | None, ...] = ()
    history_available: bool = True
    updated_at: float | None = None

    @classmethod
    def fresh(cls) -> RoundState:
        """A first-run state: no rounds, no scores, no measured anything."""
        return cls()

    @property
    def next_round(self) -> int:
        """The 1-based index of the round Algorithm 1 should run next."""
        return self.round + 1

    def with_round(self, *, round_index: int, score: float | None, candidate_id: str | None, incumbent_score: float | None, incumbent_cost: float | None, incumbent_candidate_id: str | None) -> RoundState:
        """Fold one completed round into the state, advancing ``S*`` correctly.

        **An unmeasured round cannot erase a measurement.** When ``score`` is
        ``None`` the incumbent fields are carried forward rather than
        overwritten: a preview that measured nothing must not be able to
        discard the last real ``(Ŝ_t, Ĉ_t)`` by recording its own absence, and
        replacing a number with ``None`` would look like a fresh start rather
        than like "nothing happened".
        """
        scores = self.round_scores
        if score is not None:
            scores = scores + (score,)
            if len(scores) > MAX_ROUND_SCORES:
                scores = scores[-MAX_ROUND_SCORES:]
        best = self.best_score
        best_id = self.best_candidate_id
        if score is not None and (best is None or score > best):
            best, best_id = score, candidate_id
        if score is None:
            incumbent_score = self.incumbent_score
            incumbent_cost = self.incumbent_cost
            incumbent_candidate_id = self.incumbent_candidate_id
        return replace(
            self,
            round=round_index,
            best_score=best,
            best_candidate_id=best_id,
            incumbent_score=incumbent_score,
            incumbent_cost=incumbent_cost,
            incumbent_candidate_id=incumbent_candidate_id,
            round_scores=scores,
            updated_at=time.time(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STORE_VERSION,
            "round": self.round,
            "best_score": self.best_score,
            "best_candidate_id": self.best_candidate_id,
            "incumbent_score": self.incumbent_score,
            "incumbent_cost": self.incumbent_cost,
            "incumbent_candidate_id": self.incumbent_candidate_id,
            "round_scores": list(self.round_scores),
            "history_available": self.history_available,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoundState:
        if not isinstance(data, dict):
            raise ValueError(f"round store payload must be an object, got {type(data).__name__}")
        version = data.get("version")
        if version != STORE_VERSION:
            raise ValueError(f"round store version {version!r} is not the supported version {STORE_VERSION}")
        raw_scores = data.get("round_scores", [])
        if not isinstance(raw_scores, list):
            raise ValueError(f"round_scores must be a list, got {type(raw_scores).__name__}")
        scores: list[float | None] = []
        for item in raw_scores:
            if item is None:
                scores.append(None)
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                scores.append(float(item))
            else:
                raise ValueError(f"round_scores entries must be a number or null, got {item!r}")
        round_index = data.get("round", 0)
        if isinstance(round_index, bool) or not isinstance(round_index, int) or round_index < 0:
            raise ValueError(f"round must be an integer >= 0, got {round_index!r}")
        return cls(
            round=round_index,
            best_score=_optional_float(data.get("best_score"), "best_score"),
            best_candidate_id=data.get("best_candidate_id"),
            incumbent_score=_optional_float(data.get("incumbent_score"), "incumbent_score"),
            incumbent_cost=_optional_float(data.get("incumbent_cost"), "incumbent_cost"),
            incumbent_candidate_id=data.get("incumbent_candidate_id"),
            round_scores=tuple(scores),
            history_available=bool(data.get("history_available", True)),
            updated_at=_optional_float(data.get("updated_at"), "updated_at"),
        )


def _optional_float(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number or null, got {value!r}")
    return float(value)


class RrsiRoundStore:
    """The durable carrier for ``S*``, the incumbent measurement and the score series.

    Loaded lazily at first use so a test-set ``ALPHA_HOME`` is honoured.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._explicit_path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._state: RoundState | None = None
        self._load_error: str | None = None
        self._fresh = False
        self._loaded = False

    @property
    def path(self) -> Path:
        """The store file (resolved at first use)."""
        if self._explicit_path is not None:
            return self._explicit_path
        return round_store_path()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        path = self.path
        try:
            blob = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # Absent is not corrupt: a first run has no recorded rounds.
            self._state, self._load_error, self._fresh, self._loaded = RoundState.fresh(), None, True, True
            return
        except OSError as exc:
            self._state, self._load_error, self._fresh, self._loaded = None, f"{type(exc).__name__}: {exc}", False, True
            logger.warning("RRSI round store %s could not be read; S* and the incumbent measurement are unknown, not defaulted", path, exc_info=True)
            return
        try:
            parsed = RoundState.from_dict(json.loads(blob))
        except (ValueError, TypeError) as exc:
            self._state, self._load_error, self._fresh, self._loaded = None, f"{type(exc).__name__}: {exc}", False, True
            logger.warning("RRSI round store %s is malformed; S* and the incumbent measurement are unknown, not defaulted", path, exc_info=True)
            return
        self._state, self._load_error, self._fresh, self._loaded = parsed, None, False, True

    @property
    def readable(self) -> bool:
        """``False`` only when the store exists but could not be parsed or read."""
        self._ensure_loaded()
        return self._state is not None

    @property
    def fresh(self) -> bool:
        """``True`` when the store is a first run (no file on disk)."""
        self._ensure_loaded()
        return self._fresh

    @property
    def state(self) -> RoundState | None:
        """The loaded state, or ``None`` when the store is unreadable."""
        self._ensure_loaded()
        return self._state

    @property
    def load_error(self) -> str | None:
        self._ensure_loaded()
        return self._load_error

    @property
    def best_score(self) -> float | None:
        """``S*``; ``None`` when unreadable **or** when no score has been measured yet."""
        self._ensure_loaded()
        return None if self._state is None else self._state.best_score

    @property
    def round_scores(self) -> tuple[float | None, ...]:
        """Best score per round; empty when unreadable (not ``None``, since a caller that cannot tell the two apart should still see a bounded series)."""
        self._ensure_loaded()
        return () if self._state is None else self._state.round_scores

    @property
    def round_index(self) -> int:
        """Completed rounds; ``0`` when unreadable or fresh."""
        self._ensure_loaded()
        return 0 if self._state is None else self._state.round

    @property
    def next_round(self) -> int:
        """The 1-based index of the round Algorithm 1 should run next.

        Unreadable reports ``1`` rather than ``0``: rounds are 1-based, so ``0``
        would be a round number that can never occur and a caller would treat
        the store's refusal to be read as "no rounds have ever started".
        """
        self._ensure_loaded()
        return 1 if self._state is None else self._state.next_round

    def status(self) -> dict[str, Any]:
        """Honest health block: ``best_score`` is ``null`` when unknown, never ``0``."""
        self._ensure_loaded()
        return {
            "path": str(self.path),
            "readable": self._state is not None,
            "fresh": self._fresh,
            "load_error": self._load_error,
            "round": self._state.round if self._state is not None else None,
            "best_score": self._state.best_score if self._state is not None else None,
            "incumbent_score": self._state.incumbent_score if self._state is not None else None,
            "round_score_count": len(self._state.round_scores) if self._state is not None else None,
        }

    def save(self, state: RoundState) -> DurableWrite:
        """Atomically replace the store; report whether the write landed.

        Failure never raises: the caller keeps its in-memory state and learns
        from the return value that nothing was persisted.
        """
        if not isinstance(state, RoundState):
            return DurableWrite(ok=False, error=f"save() takes a RoundState, got {type(state).__name__}", path=str(self.path))
        path = self.path
        payload = json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("RRSI round store write to %s did not land: %s", path, exc, exc_info=True)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return DurableWrite(ok=False, error=f"{type(exc).__name__}: {exc}", path=str(path))
        with self._lock:
            self._state = state
            self._load_error = None
            self._fresh = False
            self._loaded = True
        return DurableWrite(ok=True, path=str(path))

    def record_round(self, *, round_index: int, score: float | None, candidate_id: str | None, incumbent_score: float | None, incumbent_cost: float | None, incumbent_candidate_id: str | None, history_available: bool = True) -> DurableWrite:
        """Fold one completed round in and persist it; returns the write result.

        :class:`RoundState` values are immutable, so a failed write leaves the
        previously loaded state exactly as it was — a half-applied round
        cannot exist.
        """
        self._ensure_loaded()
        if self._state is None:
            return DurableWrite(ok=False, error=f"round store is unreadable ({self._load_error}); refusing to overwrite state that could not be read", path=str(self.path))
        updated = self._state.with_round(
            round_index=round_index,
            score=score,
            candidate_id=candidate_id,
            incumbent_score=incumbent_score,
            incumbent_cost=incumbent_cost,
            incumbent_candidate_id=incumbent_candidate_id,
        )
        if not history_available:
            updated = replace(updated, history_available=False)
        return self.save(updated)

    def disclosure(self) -> dict[str, Any]:
        """A bounded, non-measurement disclosure of the store's own health."""
        return {"store": self.status(), "round_scores": list(self.round_scores)}
