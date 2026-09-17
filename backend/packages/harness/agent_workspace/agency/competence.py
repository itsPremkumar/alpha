from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CompetenceRecord:
    domain: str
    successes: int = 0
    attempts: int = 0
    wilson_score: float = 0.5
    updated_at: float = field(default_factory=time.time)


class CompetenceTracker:
    """
    Tracks agent competence across domains using the
    Wilson Score Lower Bound for 95% statistical confidence.
    Prevents overconfidence when sample sizes are small.
    """

    def __init__(self, z: float = 1.96) -> None:
        self.z = z  # 1.96 corresponds to 95% confidence
        self.records: dict[str, CompetenceRecord] = {}

    def record_attempt(self, domain: str, success: bool) -> float:
        if domain not in self.records:
            self.records[domain] = CompetenceRecord(domain=domain)

        rec = self.records[domain]
        rec.attempts += 1
        if success:
            rec.successes += 1

        rec.wilson_score = self._compute_wilson(rec.successes, rec.attempts)
        rec.updated_at = time.time()
        return rec.wilson_score

    def get_competence(self, domain: str) -> float:
        """Returns the conservative 95% Wilson confidence lower bound."""
        if domain not in self.records or self.records[domain].attempts == 0:
            return 0.5  # Prior assumption for unseen domain
        return self.records[domain].wilson_score

    def _compute_wilson(self, successes: int, attempts: int) -> float:
        if attempts == 0:
            return 0.5

        n = float(attempts)
        p = float(successes) / n
        z = self.z

        denominator = 1.0 + (z * z) / n
        centre = p + (z * z) / (2.0 * n)
        spread = z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * n)) / n)

        lower_bound = (centre - spread) / denominator
        return round(max(0.0, min(1.0, lower_bound)), 4)

    def get_all_competences(self) -> dict[str, float]:
        return {d: r.wilson_score for d, r in self.records.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            d: {
                "successes": r.successes,
                "attempts": r.attempts,
                "empirical_rate": round(r.successes / r.attempts, 3) if r.attempts > 0 else 0.0,
                "wilson_confidence_95": r.wilson_score,
            }
            for d, r in self.records.items()
        }
