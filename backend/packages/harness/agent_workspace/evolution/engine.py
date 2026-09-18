"""Bounded evolution engine: improve versioned surfaces, never the core.

Candidates (skill/prompt/routing variants) run isolated against a benchmark
suite, face a promotion gate (strictly better, no regressions, human
approval for production), and roll back on failure. Permission policy,
secrets, and run-admission are never evolvable surfaces.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

EvolvableSurface = Literal["skill", "prompt", "routing", "memory_retrieval"]
CandidateStatus = Literal["candidate", "benchmarking", "gated", "promoted", "rejected", "rolled_back"]

FORBIDDEN_SURFACES = frozenset({"permission_policy", "secrets", "run_admission", "auth"})


@dataclass
class EvolCandidate:
    candidate_id: str
    surface: str
    target: str
    payload: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    status: CandidateStatus = "candidate"
    benchmark: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvolutionEngine:
    def __init__(self):
        self._candidates: dict[str, EvolCandidate] = {}
        self._ledger: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def propose(self, surface: str, target: str, payload: dict[str, Any], *, parent_id: str | None = None) -> EvolCandidate:
        if surface in FORBIDDEN_SURFACES:
            raise ValueError(f"Surface '{surface}' is not evolvable.")
        cand = EvolCandidate(candidate_id=f"ev-{uuid.uuid4().hex[:10]}", surface=surface, target=target, payload=payload, parent_id=parent_id)
        with self._lock:
            self._candidates[cand.candidate_id] = cand
            self._ledger.append({"event": "proposed", "candidate_id": cand.candidate_id, "at": time.time()})
        return cand

    def record_benchmark(self, candidate_id: str, benchmark: dict[str, Any]) -> EvolCandidate | None:
        with self._lock:
            cand = self._candidates.get(candidate_id)
            if not cand:
                return None
            cand.benchmark = benchmark
            cand.status = "benchmarking"
            self._ledger.append({"event": "benchmarked", "candidate_id": candidate_id, "at": time.time()})
            return cand

    def gate(self, candidate_id: str, baseline: dict[str, Any], *, human_approved: bool = False, autonomous_mode: bool = False) -> tuple[bool, str]:
        """Promote only if strictly better than baseline with zero regressions."""
        with self._lock:
            cand = self._candidates.get(candidate_id)
            if not cand or not cand.benchmark:
                return False, "missing candidate or benchmark"
            bench, base = cand.benchmark, baseline
            if int(bench.get("failed", 1)) > int(base.get("failed", 0)):
                cand.status = "rejected"
                self._ledger.append({"event": "rejected", "candidate_id": candidate_id, "reason": "regressions", "at": time.time()})
                return False, "benchmark regressions vs baseline"
            if float(bench.get("passed", 0)) <= float(base.get("passed", 0)):
                cand.status = "rejected"
                self._ledger.append({"event": "rejected", "candidate_id": candidate_id, "reason": "not strictly better", "at": time.time()})
                return False, "not strictly better than baseline"
            if not human_approved and not autonomous_mode:
                cand.status = "gated"
                self._ledger.append({"event": "gated", "candidate_id": candidate_id, "at": time.time()})
                return False, "awaiting human approval"
            cand.status = "promoted"
            self._ledger.append({"event": "promoted", "candidate_id": candidate_id, "at": time.time()})
            return True, "promoted"

    def rollback(self, candidate_id: str, reason: str = "") -> bool:
        with self._lock:
            cand = self._candidates.get(candidate_id)
            if not cand or cand.status != "promoted":
                return False
            cand.status = "rolled_back"
            self._ledger.append({"event": "rolled_back", "candidate_id": candidate_id, "reason": reason, "at": time.time()})
            return True

    def ledger(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._ledger[-limit:])


_engine: EvolutionEngine | None = None
_engine_lock = threading.Lock()


def get_evolution_engine() -> EvolutionEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = EvolutionEngine()
        return _engine
