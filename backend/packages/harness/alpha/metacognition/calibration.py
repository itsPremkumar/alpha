from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CalibrationRecord:
    predicted_confidence: float
    actual_success: bool
    timestamp: float = field(default_factory=time.time)
    task_type: str = "general"


class ConfidenceCalibrator:
    """
    Empirical confidence calibrator.
    Tracks predicted confidence vs ground-truth success to detect
    overconfidence drift and apply calibration shrinkage.
    Bounded in-memory history (default 1000) with JSON persistence so
    calibration survives restarts and can be scoped per task type.
    """

    def __init__(self, max_records: int = 1000) -> None:
        self.records: list[CalibrationRecord] = []
        self.max_records = max(10, int(max_records))
        # Disclosure: corrupt persisted records dropped during loads (never
        # silently discarded — surfaced via stats() and to_dict()).
        self.dropped_records: int = 0

    def record_outcome(
        self,
        predicted_confidence: float,
        actual_success: bool,
        task_type: str = "general",
    ) -> None:
        self.records.append(
            CalibrationRecord(
                predicted_confidence=max(0.0, min(1.0, float(predicted_confidence))),
                actual_success=bool(actual_success),
                task_type=task_type or "general",
            )
        )
        # Bound memory: evict oldest first.
        if len(self.records) > self.max_records:
            del self.records[: len(self.records) - self.max_records]

    def compute_brier_score(self, task_type: str | None = None) -> float | None:
        """Lower is better (0.0 = perfect probabilistic calibration).

        Returns None when no matching records exist — reporting 0.0 for an
        empty history would falsely claim perfect calibration.
        """
        recs = [r for r in self.records if task_type is None or r.task_type == task_type]
        if not recs:
            return None
        total_sq_err = sum((r.predicted_confidence - (1.0 if r.actual_success else 0.0)) ** 2 for r in recs)
        return round(total_sq_err / len(recs), 4)

    def calibrate(self, raw_confidence: float, task_type: str | None = None) -> float:
        """Calibrates raw confidence based on historical empirical accuracy."""
        raw = max(0.0, min(1.0, float(raw_confidence)))
        recs = [r for r in self.records if task_type is None or r.task_type == task_type]
        if len(recs) < 3:
            return raw  # Not enough data for shrinkage

        # Compute empirical pass rate
        pass_rate = sum(1 for r in recs if r.actual_success) / len(recs)
        mean_pred = sum(r.predicted_confidence for r in recs) / len(recs)

        # Overconfidence gap
        gap = mean_pred - pass_rate
        if gap > 0.15:
            # Chronic overconfidence detected -> apply conservative penalty
            shrinkage = gap * 0.6
            calibrated = max(0.1, raw - shrinkage)
            return round(calibrated, 3)

        return raw

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        for r in self.records:
            by_type[r.task_type] = by_type.get(r.task_type, 0) + 1
        return {
            "total_records": len(self.records),
            "brier_score": self.compute_brier_score(),
            "by_task_type": by_type,
            "dropped_records": self.dropped_records,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"records": [asdict(r) for r in self.records], "max_records": self.max_records, "dropped_records": self.dropped_records}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfidenceCalibrator:
        cal = cls(max_records=int(data.get("max_records", 1000)))
        try:
            cal.dropped_records = int(data.get("dropped_records", 0) or 0)
        except (TypeError, ValueError):
            cal.dropped_records = 0
        dropped = 0
        for item in data.get("records", []):
            try:
                if not isinstance(item, dict):
                    raise TypeError(f"record is {type(item).__name__}, not an object")
                cal.records.append(
                    CalibrationRecord(
                        predicted_confidence=float(item.get("predicted_confidence", 0.0)),
                        actual_success=bool(item.get("actual_success", False)),
                        timestamp=float(item.get("timestamp", time.time())),
                        task_type=str(item.get("task_type", "general")),
                    )
                )
            except (TypeError, ValueError):
                dropped += 1
        if dropped:
            # Never silently drop corrupt persisted records: log the count and
            # keep it disclosed on the instance (stats()/to_dict()).
            cal.dropped_records += dropped
            logger.warning("Dropped %d corrupt calibration record(s) while loading persisted calibration history", dropped)
        return cal

    def save(self, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return dest

    @classmethod
    def load(cls, path: str | Path) -> ConfidenceCalibrator:
        dest = Path(path)
        data = json.loads(dest.read_text(encoding="utf-8"))
        return cls.from_dict(data)
