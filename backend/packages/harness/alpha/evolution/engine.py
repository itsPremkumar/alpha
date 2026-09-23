"""Bounded evolution engine: improve versioned surfaces, never the core.

Candidates (skill/prompt/routing variants) run isolated against a benchmark
suite, face a promotion gate (strictly better, no regressions, human
approval for production), and roll back on failure. Permission policy,
secrets, and run-admission are never evolvable surfaces.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from alpha.config.runtime_paths import runtime_home

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
        self._ledger_path = runtime_home() / "evolution" / "ledger.jsonl"
        self._load_persisted_ledger()

    def _load_persisted_ledger(self) -> None:
        """Reload previously persisted JSONL events (spec section 37).

        Corrupt or partial lines are skipped with an honest warning (the
        skipped count is logged) instead of failing engine startup; an
        unreadable file degrades to an empty in-memory ledger, also warned.
        """
        try:
            lines = self._ledger_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("Evolution ledger %s is unreadable; starting with an empty in-memory ledger: %s", self._ledger_path, exc)
            return
        loaded: list[dict[str, Any]] = []
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
            loaded.append(event)
        if skipped:
            logger.warning("Skipped %d corrupt/partial evolution ledger line(s) in %s", skipped, self._ledger_path)
        self._ledger = loaded

    def _record_ledger_event(self, event: dict[str, Any]) -> None:
        """Append an event in memory and best-effort persist it as one JSON line.

        Called under the instance lock. Persistence failures log a warning and
        never fail propose/record_benchmark/gate/rollback — the in-memory
        ledger (and the ``ledger(limit)`` API) keeps working either way.
        """
        self._ledger.append(event)
        try:
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self._ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Could not persist evolution ledger event to %s: %s", self._ledger_path, exc)

    def propose(self, surface: str, target: str, payload: dict[str, Any], *, parent_id: str | None = None) -> EvolCandidate:
        if surface in FORBIDDEN_SURFACES:
            raise ValueError(f"Surface '{surface}' is not evolvable.")
        cand = EvolCandidate(candidate_id=f"ev-{uuid.uuid4().hex[:10]}", surface=surface, target=target, payload=payload, parent_id=parent_id)
        with self._lock:
            self._candidates[cand.candidate_id] = cand
            self._record_ledger_event({"event": "proposed", "candidate_id": cand.candidate_id, "at": time.time()})
        return cand

    def record_benchmark(self, candidate_id: str, benchmark: dict[str, Any]) -> EvolCandidate | None:
        with self._lock:
            cand = self._candidates.get(candidate_id)
            if not cand:
                return None
            cand.benchmark = benchmark
            cand.status = "benchmarking"
            self._record_ledger_event({"event": "benchmarked", "candidate_id": candidate_id, "at": time.time()})
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
                self._record_ledger_event({"event": "rejected", "candidate_id": candidate_id, "reason": "regressions", "at": time.time()})
                return False, "benchmark regressions vs baseline"
            if float(bench.get("passed", 0)) <= float(base.get("passed", 0)):
                cand.status = "rejected"
                self._record_ledger_event({"event": "rejected", "candidate_id": candidate_id, "reason": "not strictly better", "at": time.time()})
                return False, "not strictly better than baseline"
            if not human_approved and not autonomous_mode:
                cand.status = "gated"
                self._record_ledger_event({"event": "gated", "candidate_id": candidate_id, "at": time.time()})
                return False, "awaiting human approval"
            cand.status = "promoted"
            self._record_ledger_event({"event": "promoted", "candidate_id": candidate_id, "at": time.time()})
            return True, "promoted"

    def rollback(self, candidate_id: str, reason: str = "") -> bool:
        with self._lock:
            cand = self._candidates.get(candidate_id)
            if not cand or cand.status != "promoted":
                return False
            cand.status = "rolled_back"
            self._record_ledger_event({"event": "rolled_back", "candidate_id": candidate_id, "reason": reason, "at": time.time()})
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
