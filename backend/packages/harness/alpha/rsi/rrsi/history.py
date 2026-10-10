"""Regularizer **P2** — the evidence-aware credit-assignment history ``L_t``.

RRSI's second proposal-side regularizer keeps a per-attempt ledger of *what was
changed, which component it touched, and what it actually did to the score and
the cost*, so that later rounds propose toward components with demonstrated
credit instead of toward whichever component the prompt happened to mention.

Ledger record (one JSON object per line, appended, never rewritten):

    ``ℓ`` component · ``h`` hypothesis id · ``d`` payload/diff hash ·
    ``ΔS`` score delta · ``ΔC`` cost delta · ``a`` accepted flag

Binding rules (house durability doctrine — ``continuous/store.py``,
``continual/state.py``, ``groups/claims.py``):

* **An unreadable ledger reports ``unknown``, never empty.** ``entries`` is
  ``None`` while :attr:`RrsiHistory.readable` is ``False``, and every derived
  query returns ``None`` in that state. An empty tuple means "read and found
  empty"; ``None`` means "could not be read" — the two lead to opposite
  decisions and must never collapse into one value.
* **Absent is not corrupt.** A missing file is a first run: readable, empty,
  not degraded.
* **A malformed line is skipped and counted**, not dropped silently and not
  fatal — the JSONL shape means the surrounding records are still trustworthy.
  The count surfaces in :meth:`status` so a ledger rotting line by line is
  visible instead of slowly losing credit history unnoticed.
* **``ΔS``/``ΔC`` are ``None`` when unmeasured.** They are never defaulted to
  ``0.0``, which would read as "no change" rather than "not measured", and
  never to a plausible-looking number.
* **Persistence failure never raises.** ``append`` reports ``False`` with the
  real reason (lineage-store precedent): the in-memory record still exists so
  the current cycle can use it, but nothing claims the write landed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.rsi.rrsi.components import COMPONENTS

logger = logging.getLogger(__name__)

__all__ = ["GainWindow", "HISTORY_FILE_NAME", "HistoryEntry", "RrsiHistory", "history_path"]

HISTORY_FILE_NAME = "rrsi_history.jsonl"

#: Fields every stored record must carry. A record missing one is malformed
#: and is skipped with a count — never back-filled with a default.
REQUIRED_FIELDS: frozenset[str] = frozenset({"round", "candidate_id", "component", "diff_hash", "accepted", "recorded_at"})


def history_path(root: Path | str | None = None) -> Path:
    """``runtime_home()/rsi/rrsi_history.jsonl`` (``root`` overrides the base)."""
    base = Path(root) if root is not None else runtime_home()
    return base / "rsi" / HISTORY_FILE_NAME


@dataclass(frozen=True)
class HistoryEntry:
    """One attempt recorded in ``L_t``.

    ``delta_score``/``delta_cost`` are ``None`` when the corresponding
    quantity was never measured; ``accepted`` is the promotion verdict actually
    reached, which is a separate fact from either delta (a candidate can be
    rejected for a reason unrelated to its score).
    """

    round: int
    candidate_id: str
    component: str
    diff_hash: str
    accepted: bool
    delta_score: float | None = None
    delta_cost: float | None = None
    hypothesis_id: str = ""
    recorded_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.component not in COMPONENTS:
            raise ValueError(f"history entry component {self.component!r} is not in K = {list(COMPONENTS)}; the ledger may only record attributable attempts.")
        if isinstance(self.accepted, bool) is False:
            raise ValueError(f"history entry 'accepted' must be a real bool; got {self.accepted!r}.")
        for name in ("delta_score", "delta_cost"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ValueError(f"history entry {name!r} must be a number or None (None = not measured); got {value!r}.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "candidate_id": self.candidate_id,
            "component": self.component,
            "diff_hash": self.diff_hash,
            "hypothesis_id": self.hypothesis_id,
            "delta_score": self.delta_score,
            "delta_cost": self.delta_cost,
            "accepted": self.accepted,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryEntry:
        missing = sorted(REQUIRED_FIELDS - set(data))
        if missing:
            raise ValueError(f"history record missing required field(s): {', '.join(missing)}")
        return cls(
            round=int(data["round"]),
            candidate_id=str(data["candidate_id"]),
            component=str(data["component"]),
            diff_hash=str(data["diff_hash"]),
            accepted=data["accepted"],
            delta_score=data.get("delta_score"),
            delta_cost=data.get("delta_cost"),
            hypothesis_id=str(data.get("hypothesis_id", "")),
            recorded_at=float(data["recorded_at"]),
        )


@dataclass(frozen=True)
class GainWindow:
    """``g_t(ℓ)`` measured over one pruning window, with the counts behind it.

    ``best`` is ``None`` when no record in the window carried a measured
    ``ΔS``; under the paper's ``max ∅ = −∞`` that still satisfies
    ``g_t(ℓ) ≤ 0``, but the *reason* differs from a component that was
    measured and still failed to improve — hence ``records`` and ``measured``
    are both reported rather than collapsed into one number.
    """

    component: str
    window: int
    top_round: int
    floor_round: int
    records: int
    measured: int
    best: float | None

    @property
    def positive(self) -> bool:
        """Whether the component earned a strictly positive measured gain in-window."""
        return self.best is not None and self.best > 0

    @property
    def in_window(self) -> bool:
        """Whether any attempt for this component falls inside the window."""
        return self.records > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "window": self.window,
            "top_round": self.top_round,
            "floor_round": self.floor_round,
            "records": self.records,
            "measured": self.measured,
            "best_delta_score": self.best,
            "positive": self.positive,
        }


class RrsiHistory:
    """The durable credit-assignment ledger, loaded once and appended to.

    ``entries`` is ``None`` when the ledger could not be read — see the module
    docstring. All derived queries are therefore written to *first* check
    :attr:`readable` and to return ``None`` rather than an empty result when it
    is ``False``.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._explicit_path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._entries: tuple[HistoryEntry, ...] | None = None
        self._load_error: str | None = None
        self._skipped_lines = 0
        self._loaded = False

    @property
    def path(self) -> Path:
        """The ledger file (resolved at first use so a test-set ``ALPHA_HOME`` is honoured)."""
        if self._explicit_path is not None:
            return self._explicit_path
        return history_path()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        path = self.path
        try:
            blob = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # Absent is not corrupt: a first run has an empty, readable ledger.
            self._entries, self._load_error, self._skipped_lines = (), None, 0
            self._loaded = True
            return
        except OSError as exc:
            self._entries, self._load_error, self._skipped_lines = None, f"{type(exc).__name__}: {exc}", 0
            self._loaded = True
            logger.warning("RRSI history ledger %s could not be read; derived queries will report unknown, not empty", path, exc_info=True)
            return
        parsed: list[HistoryEntry] = []
        skipped = 0
        for line in blob.splitlines():
            if not line.strip():
                continue
            try:
                parsed.append(HistoryEntry.from_dict(json.loads(line)))
            except (ValueError, TypeError, KeyError):
                skipped += 1
        self._entries, self._load_error, self._skipped_lines = tuple(parsed), None, skipped
        self._loaded = True
        if skipped:
            logger.warning("Skipped %d malformed line(s) in RRSI history ledger %s (count reported, never repaired)", skipped, path)

    @property
    def readable(self) -> bool:
        """``False`` only when the ledger exists but could not be read."""
        self._ensure_loaded()
        return self._entries is not None

    @property
    def entries(self) -> tuple[HistoryEntry, ...] | None:
        """Every record oldest-first, or ``None`` when the ledger is unreadable."""
        self._ensure_loaded()
        return self._entries

    @property
    def load_error(self) -> str | None:
        """The real read failure, or ``None`` when the ledger was readable."""
        self._ensure_loaded()
        return self._load_error

    @property
    def skipped_lines(self) -> int:
        """Malformed records skipped on load (0 when the ledger is unreadable)."""
        self._ensure_loaded()
        return self._skipped_lines

    def status(self) -> dict[str, Any]:
        """Honest health block: ``entries`` is ``null`` when unreadable, never ``0``."""
        self._ensure_loaded()
        return {
            "path": str(self.path),
            "readable": self._entries is not None,
            "count": len(self._entries) if self._entries is not None else None,
            "load_error": self._load_error,
            "skipped_lines": self._skipped_lines,
            "degraded": bool(self._load_error) or self._skipped_lines > 0,
        }

    def append(self, entry: HistoryEntry) -> bool:
        """Append one record; return whether the write actually landed.

        The in-memory copy is updated regardless so the current cycle can keep
        using it, but the return value is what a caller may report as durable.
        """
        if not isinstance(entry, HistoryEntry):
            raise ValueError("append() takes a HistoryEntry.")
        self._ensure_loaded()
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            logger.warning("RRSI history append to %s did not land: %s", path, exc, exc_info=True)
            with self._lock:
                if self._entries is not None:
                    self._entries = self._entries + (entry,)
            return False
        with self._lock:
            if self._entries is None:
                # A previously unreadable ledger that just accepted a write is
                # still not "read": only the new record would be visible.
                return False
            self._entries = self._entries + (entry,)
        return True

    # -- derived credit queries -------------------------------------------------
    # Every one of these returns None when the ledger is unreadable. The
    # distinction between "None (unknown)" and "0/empty (known)" is the whole
    # point of the durability doctrine above.

    def exercised_components(self) -> frozenset[str] | None:
        """``T_t`` — components with at least one recorded attempt; ``None`` = unknown."""
        entries = self.entries
        if entries is None:
            return None
        return frozenset(entry.component for entry in entries)

    def gain_window(self, component: str, *, window: int) -> GainWindow | None:
        """``g_t(ℓ) = max{ΔS_i : ℓ_i = ℓ, t − t_i ≤ n_prune}`` for one component.

        Returns ``None`` when the ledger is unreadable. Otherwise returns a
        :class:`GainWindow` whose ``best`` is ``None`` exactly when the
        component has **no measured** ``ΔS`` inside the window — which, under
        the paper's ``max ∅ = −∞`` convention, still satisfies ``g_t(ℓ) ≤ 0``.

        ``records`` (every attempt in the window) and ``measured`` (those
        carrying a real ``ΔS``) travel separately so a caller can say *why* a
        component looks unproductive: it earned a bad number, or it never
        produced a number at all. Those are different facts about a mechanism
        and must not be merged into one count.
        """
        entries = self.entries
        if entries is None:
            return None
        if window < 1:
            raise ValueError(f"window must be >= 1; got {window}.")
        top_round = max((entry.round for entry in entries), default=None)
        if top_round is None:
            return GainWindow(component=component, window=window, top_round=0, floor_round=1, records=0, measured=0, best=None)
        floor_round = top_round - window + 1
        in_window = [entry for entry in entries if entry.component == component and entry.round >= floor_round]
        measured = [entry.delta_score for entry in in_window if entry.delta_score is not None]
        return GainWindow(
            component=component,
            window=window,
            top_round=top_round,
            floor_round=floor_round,
            records=len(in_window),
            measured=len(measured),
            best=max(measured) if measured else None,
        )

    def recent_best_gain(self, component: str, *, window: int) -> tuple[float | None, int] | None:
        """Best ``ΔS`` for ``component`` over the last ``window`` rounds.

        Thin wrapper over :meth:`gain_window` for callers that only need
        ``(best, measured_count)``. Returns ``None`` when the ledger is
        unreadable; ``best`` is ``None`` when nothing in the window was
        measured — a record with ``delta_score=None`` is not evidence of "no
        gain", so it is excluded rather than counted as ``0.0``.
        """
        stat = self.gain_window(component, window=window)
        if stat is None:
            return None
        return stat.best, stat.measured

    def attempts(self, component: str | None = None) -> int | None:
        """Recorded attempts for ``component`` (or all components); ``None`` = unknown."""
        entries = self.entries
        if entries is None:
            return None
        if component is None:
            return len(entries)
        return sum(1 for entry in entries if entry.component == component)

    def accepted_components(self) -> frozenset[str] | None:
        """Components with at least one accepted attempt; ``None`` = unknown."""
        entries = self.entries
        if entries is None:
            return None
        return frozenset(entry.component for entry in entries if entry.accepted)

    def unmeasured_deltas(self) -> int | None:
        """Attempts recorded without a measured ``ΔS``; ``None`` = unknown.

        Surfaced so a search that never measures anything is visible: a ledger
        full of ``None`` deltas yields no credit assignment at all, and a
        consumer that reported ``best_gain = 0`` instead would be converting
        "never measured" into "measured as no change".
        """
        entries = self.entries
        if entries is None:
            return None
        return sum(1 for entry in entries if entry.delta_score is None)

    def to_disclosure(self) -> dict[str, Any]:
        """A bounded, non-measurement disclosure of the ledger's own health."""
        status = self.status()
        return {
            "ledger": status,
            "exercised_components": sorted(self.exercised_components()) if self.exercised_components() is not None else None,
            "attempts": self.attempts(),
            "attempts_without_measured_delta": self.unmeasured_deltas(),
        }
