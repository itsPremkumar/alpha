"""Durable forecast ledger: forecast → outcome pairs, append-only.

The ledger exists for one reason — **calibration needs history that survives a
restart**. A predictor that can only see the current process cannot be checked
against its own past, and an uncheckable predictor is the thing this whole
package exists to avoid.

It follows :class:`alpha.rsi.rrsi.history.RrsiHistory`'s contract exactly,
because that is the established house pattern for a JSONL ledger under
``runtime_home()/rsi/``:

* **An unreadable ledger reports unknown, never empty.** :attr:`entries` is
  ``None`` while :attr:`readable` is ``False``. An empty tuple means "read and
  found empty"; ``None`` means "could not be read" — the two lead to opposite
  decisions (predict from a prior vs. refuse to predict) and must never
  collapse into one value.
* **Absent is not corrupt.** A missing file is a first run: readable, empty.
* **A malformed line is skipped and counted**, never dropped silently and never
  fatal — the JSONL shape means the surrounding records are still trustworthy.
  The count surfaces in :meth:`ForecastLedger.status` so a ledger rotting line by
  line is visible instead of slowly losing history unnoticed.
* **Persistence failure never raises.** :meth:`append` returns ``False`` with the
  real reason; the caller keeps the in-memory record and learns the write did not
  land. Claiming durability that did not happen is worse than losing a record.
* **Single-process.** Atomic append and restart-recoverable for one Gateway; not
  a cross-process exactly-once store, and never described as one.

Two record kinds share the file, distinguished by ``kind``: ``"forecast"`` and
``"outcome"``. Keeping them in one append-only stream means a forecast and the
outcome that later measures it are read in the same order they happened, which
is what makes pairing by candidate id meaningful.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.rsi.predictive.contracts import (
    MEASURED_EVIDENCE_KIND,
    ObservedOutcome,
    OutcomePrediction,
)

logger = logging.getLogger(__name__)

__all__ = [
    "FORECAST_LEDGER_FILE_NAME",
    "ForecastLedger",
    "LedgerWrite",
    "forecast_ledger_path",
]

FORECAST_LEDGER_FILE_NAME = "predictions.jsonl"

#: Record kinds this ledger understands. An unknown kind is counted as malformed
#: rather than guessed at.
FORECAST_KIND = "forecast"
OUTCOME_KIND = "outcome"
KNOWN_KINDS = frozenset({FORECAST_KIND, OUTCOME_KIND})

#: The evidence label a stored outcome must carry to count as a measurement.
#: Anything else — a preview's ``simulated``, a gate's ``unverified`` — is
#: recorded but never treated as a measured label, which is what stops the
#: predictor from learning from numbers nobody verified.
_ELIGIBLE_OUTCOME_EVIDENCE = frozenset({MEASURED_EVIDENCE_KIND})


def forecast_ledger_path(root: Path | str | None = None) -> Path:
    """``runtime_home()/rsi/predictions.jsonl`` (``root`` overrides the base)."""
    base = Path(root) if root is not None else runtime_home()
    return base / "rsi" / FORECAST_LEDGER_FILE_NAME


@dataclass(frozen=True)
class LedgerWrite:
    """Whether an append actually reached the disk, and why not if it did not."""

    ok: bool
    error: str | None = None
    path: str = ""

    def __bool__(self) -> bool:
        return self.ok


class ForecastLedger:
    """Append-only store of forecast/outcome pairs, loaded once and appended to."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._explicit_path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._entries: tuple[dict[str, Any], ...] | None = None
        self._load_error: str | None = None
        self._skipped_lines = 0
        self._loaded = False

    @property
    def path(self) -> Path:
        """The ledger file (resolved at first use so a test-set ``ALPHA_HOME`` is honoured)."""
        if self._explicit_path is not None:
            return self._explicit_path
        return forecast_ledger_path()

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
            logger.warning("Predictive RSI forecast ledger %s could not be read; calibration will report unknown, not empty", path, exc_info=True)
            return

        entries: list[dict[str, Any]] = []
        skipped = 0
        for line in blob.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except ValueError:
                skipped += 1
                continue
            if not isinstance(parsed, dict) or parsed.get("kind") not in KNOWN_KINDS:
                skipped += 1
                continue
            entries.append(parsed)
        self._entries, self._load_error, self._skipped_lines = tuple(entries), None, skipped
        self._loaded = True
        if skipped:
            logger.warning("Predictive RSI forecast ledger %s had %d malformed line(s); the remaining %d record(s) are still trusted", path, skipped, len(entries))

    @property
    def readable(self) -> bool:
        """``False`` only when the ledger exists but could not be parsed or read."""
        self._ensure_loaded()
        return self._entries is not None

    @property
    def load_error(self) -> str | None:
        self._ensure_loaded()
        return self._load_error

    @property
    def entries(self) -> tuple[dict[str, Any], ...] | None:
        """All records, or ``None`` when the ledger could not be read."""
        self._ensure_loaded()
        return self._entries

    def forecasts(self) -> tuple[OutcomePrediction, ...] | None:
        """Stored forecasts; ``None`` when unreadable, empty when genuinely empty."""
        self._ensure_loaded()
        if self._entries is None:
            return None
        parsed: list[OutcomePrediction] = []
        for record in self._entries:
            if record.get("kind") != FORECAST_KIND:
                continue
            try:
                parsed.append(OutcomePrediction.from_dict(record))
            except ValueError:
                continue
        return tuple(parsed)

    def outcomes(self) -> tuple[ObservedOutcome, ...] | None:
        """Stored outcomes; ``None`` when unreadable, empty when genuinely empty."""
        self._ensure_loaded()
        if self._entries is None:
            return None
        parsed: list[ObservedOutcome] = []
        for record in self._entries:
            if record.get("kind") != OUTCOME_KIND:
                continue
            try:
                parsed.append(ObservedOutcome.from_dict(record))
            except ValueError:
                continue
        return tuple(parsed)

    def measured_outcomes(self) -> tuple[ObservedOutcome, ...] | None:
        """Only outcomes carrying a genuinely measured label.

        This is the gate between *recorded* and *trusted*: a stored
        ``simulated``/``unverified`` outcome is visible in :meth:`outcomes` for
        the audit trail and excluded here, so calibration can never be computed
        against a number nobody measured.
        """
        stored = self.outcomes()
        if stored is None:
            return None
        return tuple(outcome for outcome in stored if outcome.evidence_kind in _ELIGIBLE_OUTCOME_EVIDENCE)

    def status(self) -> dict[str, Any]:
        """Honest health block: counts are ``None`` when unknown, never ``0``."""
        self._ensure_loaded()
        if self._entries is None:
            return {
                "path": str(self.path),
                "readable": False,
                "record_count": None,
                "forecast_count": None,
                "outcome_count": None,
                "measured_outcome_count": None,
                "skipped_lines": None,
                "load_error": self._load_error,
            }
        forecasts = self.forecasts() or ()
        outcomes = self.outcomes() or ()
        measured = self.measured_outcomes() or ()
        return {
            "path": str(self.path),
            "readable": True,
            "record_count": len(self._entries),
            "forecast_count": len(forecasts),
            "outcome_count": len(outcomes),
            "measured_outcome_count": len(measured),
            "skipped_lines": self._skipped_lines,
            "load_error": None,
        }

    def append(self, record: OutcomePrediction | ObservedOutcome) -> LedgerWrite:
        """Append one forecast or outcome. Never raises; reports whether it landed.

        A failed write leaves the caller's in-memory record intact and returns
        ``ok=False`` with the real error — the alternative (raising, or reporting
        success) would either break a cycle over telemetry or claim durability
        that does not exist.
        """
        if isinstance(record, OutcomePrediction):
            payload = {"kind": FORECAST_KIND, **record.to_dict()}
        elif isinstance(record, ObservedOutcome):
            payload = {"kind": OUTCOME_KIND, **record.to_dict()}
        else:
            return LedgerWrite(ok=False, error=f"append takes an OutcomePrediction or ObservedOutcome, got {type(record).__name__}", path=str(self.path))

        path = self.path
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
        except OSError as exc:
            logger.warning("Predictive RSI forecast ledger write to %s did not land: %s", path, exc, exc_info=True)
            return LedgerWrite(ok=False, error=f"{type(exc).__name__}: {exc}", path=str(path))

        # Adopt the record in memory **only if the ledger was already loaded**.
        # Calling `_ensure_loaded()` first would read the line just written back
        # off disk and then append it again, counting every record twice for the
        # life of the process. An unloaded ledger is left unloaded so the next
        # read picks the appended line up exactly once.
        with self._lock:
            if self._loaded and self._entries is not None:
                self._entries = (*self._entries, payload)
        return LedgerWrite(ok=True, path=str(path))

    def extend(self, records: Sequence[OutcomePrediction | ObservedOutcome]) -> tuple[LedgerWrite, ...]:
        """Append several records; each result is reported individually."""
        return tuple(self.append(record) for record in records)
