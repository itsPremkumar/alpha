from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CuriosityScore:
    situation_hash: str
    score: float  # 0.0 to 1.0 (1.0 = highly curious / novel)
    novelty: float
    prediction_error: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CuriosityScorer:
    """
    Measures situation novelty and environment prediction error
    to drive active exploration when unexpected states occur.
    """

    def __init__(self) -> None:
        self.known_situations: dict[str, float] = {}  # hash -> familiarity count
        self.history: list[CuriosityScore] = []

    def score(
        self,
        situation: dict[str, Any],
        predicted_outcome: float | None = None,
        actual_outcome: float | None = None,
    ) -> CuriosityScore:
        sit_hash = self._hash_situation(situation)

        # Novelty: 1.0 if completely unseen, decays with repeated encounters
        familiarity = self.known_situations.get(sit_hash, 0.0)
        novelty = max(0.0, 1.0 - familiarity)

        # Prediction error: difference between expected transition and observed reality
        prediction_error = 0.0
        if predicted_outcome is not None and actual_outcome is not None:
            prediction_error = min(1.0, abs(predicted_outcome - actual_outcome))

        # Composite curiosity score
        composite = novelty * 0.6 + prediction_error * 0.4
        score = round(max(0.0, min(1.0, composite)), 4)

        # Update familiarity
        self.known_situations[sit_hash] = min(1.0, familiarity + 0.20)

        result = CuriosityScore(
            situation_hash=sit_hash,
            score=score,
            novelty=round(novelty, 4),
            prediction_error=round(prediction_error, 4),
        )
        self.history.append(result)
        return result

    def _hash_situation(self, situation: dict[str, Any]) -> str:
        s = json.dumps(situation, sort_keys=True, default=str)
        return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]
