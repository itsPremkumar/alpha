"""Measuring whether System One's confidence means what it says.

Calibration is the whole reason to use these models: a stated 0.9 should be right
about 90% of the time. That claim is worth real money — it is what lets a call
site skip the LLM entirely — and it is also **site-specific**. A model calibrated
in general can still be overconfident on Alpha's particular traces, and the only
way to know is to measure.

This module provides the two halves:

1. **Recording** — every decision, with its site label, tier, question type,
   probability/confidence and latency, appended as JSONL.
2. **Reporting** — reliability buckets (stated vs observed), Brier score,
   expected calibration error, and per-site coverage.

The outcome half is deliberately separate. Most sites have no automatic ground
truth, so :func:`record_outcome` is provided for those that eventually get one
(a test passes, a file exists, a human accepts). Sites without outcomes still
contribute coverage and latency, which is enough to answer "is this worth
turning on?"

Contract: recording is best-effort. A full disk, a bad path, or a concurrent
writer must never affect a decision.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LOG_NAME = "system_one_decisions.jsonl"

#: Reliability bucket edges. Calibration is reported per bucket because a single
#: average hides the failure that matters (confidently wrong).
BUCKET_EDGES = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01)

#: Minimum decisions in a bucket before its `observed` rate means anything.
#: Small samples are reported but flagged — see `Bucket.reliable`.
MIN_BUCKET_COUNT = 20

#: Bounded retry budget for the Windows (non-blocking) log lock.
_LOCK_RETRIES = 20
_LOCK_SLEEP = 0.05


@dataclass
class DecisionRecord:
    """One System One decision."""

    ts: float
    site: str
    tier: str
    question_id: str
    type: str
    value: Any
    confidence: float | None
    threshold: float
    latency_ms: float
    model: str = ""
    shadow: bool = False
    #: Filled in later by :func:`record_outcome`, when the site learns the truth.
    outcome: bool | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def acted(self) -> bool:
        """Did this decision clear the threshold (i.e. was it used)?"""
        if self.shadow:
            return False
        if self.confidence is not None:
            return self.confidence >= self.threshold
        if self.type == "boolean":
            try:
                return abs(float(self.value) - 0.5) * 2 >= self.threshold
            except (TypeError, ValueError):
                return False
        return False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolve_log_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute():
        try:
            from alpha.config.paths import STATE_DIR

            path = Path(STATE_DIR) / path
        except Exception:
            path = Path.cwd() / path
    return path


class DecisionRecorder:
    """Append-only JSONL decision log. Never raises into the caller."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._warned = False
        #: Newest timestamp recorded per site, so a caller that later learns the
        #: truth can find its own decision without having to thread an id back
        #: through the whole call stack. In-process only — see record_recent_outcome.
        self._last_ts: dict[str, float] = {}

    @property
    def path(self) -> Path | None:
        return self._path

    def last_ts(self, site: str) -> float | None:
        """Timestamp of the most recent decision recorded for `site`."""
        return self._last_ts.get(site)

    def record(self, record: DecisionRecord) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict(), default=str) + "\n")
            self._last_ts[record.site] = record.ts
        except Exception as exc:
            if not self._warned:
                logger.warning("System One decision log unavailable (%s); continuing without recording.", exc)
                self._warned = True


_recorder: DecisionRecorder | None = None


def get_recorder() -> DecisionRecorder:
    """Process-wide recorder, configured from the active System One config."""
    global _recorder
    if _recorder is None:
        path: Path | None = None
        try:
            from alpha.config import get_app_config

            cfg = get_app_config().system_one
            # Shadow mode implies recording: running shadow with no log would
            # measure nothing, which is the one thing shadow mode is for.
            if cfg.record_decisions or cfg.shadow_mode:
                path = _resolve_log_path(cfg.calibration_log_path or DEFAULT_LOG_NAME)
        except Exception:
            path = None
        _recorder = DecisionRecorder(path)
    return _recorder


def configure_recorder(path: Path | str | None) -> None:
    """Point the recorder somewhere else (tests, scripts)."""
    global _recorder
    _recorder = DecisionRecorder(Path(path) if path else None)


def reset_recorder() -> None:
    global _recorder
    _recorder = None


def load_records(path: Path | str) -> list[DecisionRecord]:
    """Read a JSONL log, skipping anything malformed."""
    records: list[DecisionRecord] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        try:
            records.append(
                DecisionRecord(
                    ts=float(raw.get("ts", 0.0)),
                    site=str(raw.get("site", "")),
                    tier=str(raw.get("tier", "")),
                    question_id=str(raw.get("question_id", "")),
                    type=str(raw.get("type", "")),
                    value=raw.get("value"),
                    confidence=raw.get("confidence"),
                    threshold=float(raw.get("threshold", 0.0)),
                    latency_ms=float(raw.get("latency_ms", 0.0)),
                    model=str(raw.get("model", "")),
                    shadow=bool(raw.get("shadow", False)),
                    outcome=raw.get("outcome"),
                    meta=raw.get("meta") or {},
                )
            )
        except (TypeError, ValueError):
            continue
    return records


@contextlib.contextmanager
def _file_lock(path: Path):
    """Best-effort cross-process advisory lock around a log rewrite.

    Deliberately never raises: if locking is unavailable or contended past its
    retry budget, the caller proceeds unlocked rather than failing a decision.
    A lost outcome is a nuisance; a crashed request is not.
    """
    try:
        handle = path.open("a+")
    except OSError:
        yield
        return
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            try:
                import msvcrt

                for _ in range(_LOCK_RETRIES):
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(_LOCK_SLEEP)
            except (ImportError, OSError):
                pass  # no locking available; proceed best-effort
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass


def record_outcome(path: Path | str, ts: float, site: str, outcome: bool) -> int:
    """Attach a known outcome to earlier decisions. Returns how many matched.

    Matching is by ``(site, ts)`` because that pair is unique per request and
    survives the caller having no record ids.
    """
    records = load_records(path)
    if not records:
        return 0
    matched = 0
    for record in records:
        if record.site == site and abs(record.ts - ts) < 1e-6:
            record.outcome = outcome
            matched += 1
    if not matched:
        return 0

    # Read-modify-write over a log another process may be appending to, so it
    # is guarded by a lock file and committed with an atomic replace. Without
    # both, a concurrent append lands between our read and our write and is
    # silently lost — the worst possible failure for a measurement tool.
    target = Path(path)
    lock_path = target.with_suffix(target.suffix + ".lock")
    try:
        with _file_lock(lock_path):
            fresh = load_records(target)
            for record in fresh:
                if record.site == site and abs(record.ts - ts) < 1e-6:
                    record.outcome = outcome
            tmp = target.with_suffix(target.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                for record in fresh:
                    handle.write(json.dumps(record.to_dict(), default=str) + "\n")
            os.replace(tmp, target)
    except OSError as exc:
        logger.warning("Could not persist outcome: %s", exc)
        return 0
    return matched


def record_recent_outcome(site: str, outcome: bool) -> int:
    """Attach `outcome` to the most recent decision made at `site`.

    This exists because the caller that learns the truth is usually not the code
    that made the decision — a browser agent executes a step and only afterwards
    discovers whether the action it chose actually worked. Threading a record id
    back through that call stack would mean changing every signature in between,
    so instead the recorder remembers the newest timestamp per site and this
    resolves it.

    **In-process only**: the timestamp lookup is a dict on the live recorder, so
    it works when the decision and the outcome happen in the same process (the
    normal case). It returns 0, harmlessly, if nothing was recorded. Use
    :func:`record_outcome` directly when you have the timestamp across processes.
    """
    try:
        recorder = get_recorder()
        ts = recorder.last_ts(site)
        path = recorder.path
        if ts is None or path is None:
            return 0
        return record_outcome(path, ts, site, outcome)
    except Exception as exc:  # pragma: no cover - measurement must never break a run
        logger.debug("Could not record outcome for %s: %s", site, exc)
        return 0


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _stated_probability(record: DecisionRecord) -> float | None:
    """The number the model claims, on 0..1, for any question type."""
    if record.type == "boolean":
        try:
            return float(record.value)
        except (TypeError, ValueError):
            return None
    if record.confidence is not None:
        return float(record.confidence)
    return None


@dataclass
class Bucket:
    low: float
    high: float
    count: int = 0
    correct: int = 0
    mean_stated: float = 0.0

    @property
    def observed(self) -> float | None:
        return (self.correct / self.count) if self.count else None

    @property
    def gap(self) -> float | None:
        """Observed minus stated. Positive = overconfident."""
        if self.observed is None or not self.count:
            return None
        return self.mean_stated - self.observed

    @property
    def reliable(self) -> bool:
        """Is there enough data here to draw a conclusion?

        Two decisions at 0.9 that both happened to be right produce an
        "observed 1.00, gap +0.00" row that looks perfect and means nothing.
        Below the threshold the row is reported but flagged, because the failure
        mode we are guarding against is someone switching a site on because its
        numbers looked good on three samples.
        """
        return self.count >= MIN_BUCKET_COUNT


@dataclass
class CalibrationReport:
    total: int = 0
    scored: int = 0
    acted: int = 0
    buckets: list[Bucket] = field(default_factory=list)
    brier: float | None = None
    expected_calibration_error: float | None = None
    by_site: dict[str, dict[str, Any]] = field(default_factory=dict)
    mean_latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "scored": self.scored,
            "acted": self.acted,
            "brier": self.brier,
            "expected_calibration_error": self.expected_calibration_error,
            "mean_latency_ms": round(self.mean_latency_ms, 2),
            "min_bucket_count": MIN_BUCKET_COUNT,
            "buckets": [
                {
                    "range": f"{b.low:.2f}-{min(b.high, 1.0):.2f}",
                    "count": b.count,
                    "mean_stated": round(b.mean_stated, 4),
                    "observed": None if b.observed is None else round(b.observed, 4),
                    "gap": None if b.gap is None else round(b.gap, 4),
                    "reliable": b.reliable,
                }
                for b in self.buckets
            ],
            "by_site": self.by_site,
        }

    def render(self) -> str:
        lines = [
            "System One calibration",
            f"  decisions recorded : {self.total}",
            f"  with outcomes      : {self.scored}",
            f"  would have acted   : {self.acted}",
            f"  mean latency       : {self.mean_latency_ms:.0f}ms",
        ]
        if self.brier is not None:
            lines.append(f"  Brier score        : {self.brier:.4f} (lower is better)")
        if self.expected_calibration_error is not None:
            lines.append(f"  calibration error  : {self.expected_calibration_error:.4f} (0 is perfect)")
        if self.buckets:
            lines.append("")
            lines.append(f"  {'stated':<14}{'n':>6}{'observed':>11}{'gap':>9}")
            for bucket in self.buckets:
                if not bucket.count:
                    continue
                observed = "n/a" if bucket.observed is None else f"{bucket.observed:.3f}"
                gap = "n/a" if bucket.gap is None else f"{bucket.gap:+.3f}"
                flag = "" if bucket.reliable else "  (too few to judge)"
                lines.append(f"  {f'{bucket.low:.2f}-{min(bucket.high, 1.0):.2f}':<14}{bucket.count:>6}{observed:>11}{gap:>9}{flag}")
            if any(bucket.count and not bucket.reliable for bucket in self.buckets):
                lines.append(f"  (a bucket needs >= {MIN_BUCKET_COUNT} decisions before its rate means anything)")
        if self.by_site:
            lines.append("")
            lines.append(f"  {'site':<26}{'n':>6}{'acted':>8}{'scored':>8}")
            for site, stats in sorted(self.by_site.items()):
                lines.append(f"  {site:<26}{stats['count']:>6}{stats['acted']:>8}{stats['scored']:>8}")
        if not self.scored:
            lines.append("")
            lines.append("  No outcomes recorded yet — coverage and latency only.")
            lines.append("  Only `browser` records outcomes automatically; elsewhere call")
            lines.append("  record_recent_outcome(), or label a sample by hand.")
        else:
            thin = [s for s, st in self.by_site.items() if st["scored"] and st["scored"] < MIN_BUCKET_COUNT]
            if thin:
                lines.append("")
                lines.append(f"  Thin evidence (<{MIN_BUCKET_COUNT} outcomes): {', '.join(sorted(thin))}")
                lines.append("  These sites are measured but not yet trustworthy — leave them in shadow.")
        return "\n".join(lines)


def calibration_report(records: Iterable[DecisionRecord]) -> CalibrationReport:
    """Compute calibration over recorded decisions.

    Only records with a known ``outcome`` contribute to Brier / ECE — everything
    still contributes to coverage, latency and the per-site counts.
    """
    all_records = list(records)
    report = CalibrationReport(total=len(all_records))
    if not all_records:
        return report

    buckets = [Bucket(low=BUCKET_EDGES[i], high=BUCKET_EDGES[i + 1]) for i in range(len(BUCKET_EDGES) - 1)]
    brier_total = 0.0
    brier_count = 0
    ece_total = 0.0
    latency_total = 0.0

    per_site: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "acted": 0, "scored": 0})

    for record in all_records:
        stats = per_site[record.site or "(unlabelled)"]
        stats["count"] += 1
        if record.acted:
            stats["acted"] += 1
            report.acted += 1
        latency_total += record.latency_ms

        if record.outcome is None:
            continue
        stated = _stated_probability(record)
        if stated is None:
            continue

        report.scored += 1
        stats["scored"] += 1
        actual = 1.0 if record.outcome else 0.0
        brier_total += (stated - actual) ** 2
        brier_count += 1

        for bucket in buckets:
            if bucket.low <= stated < bucket.high:
                bucket.count += 1
                bucket.correct += 1 if record.outcome else 0
                bucket.mean_stated += stated
                break

    for bucket in buckets:
        if bucket.count:
            bucket.mean_stated /= bucket.count
            if bucket.observed is not None:
                ece_total += (bucket.count / max(1, report.scored)) * abs(bucket.mean_stated - bucket.observed)

    report.buckets = buckets
    report.by_site = dict(per_site)
    report.mean_latency_ms = latency_total / len(all_records)
    if brier_count:
        report.brier = round(brier_total / brier_count, 6)
        report.expected_calibration_error = round(ece_total, 6)
    return report


__all__ = [
    "BUCKET_EDGES",
    "DEFAULT_LOG_NAME",
    "MIN_BUCKET_COUNT",
    "Bucket",
    "CalibrationReport",
    "DecisionRecord",
    "DecisionRecorder",
    "calibration_report",
    "configure_recorder",
    "get_recorder",
    "load_records",
    "record_outcome",
    "record_recent_outcome",
    "reset_recorder",
]
