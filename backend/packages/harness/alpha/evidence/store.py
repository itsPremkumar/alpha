from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from threading import RLock
from typing import Any, TypeVar
from uuid import uuid4

from .models import Candidate, Evaluation, EvaluationVerdict, EvidenceKind, EvidenceRecord, Promotion, validate_text

_STORE_LOCKS: dict[str, Any] = {}
_STORE_LOCKS_GUARD = RLock()
_Record = TypeVar("_Record", EvidenceRecord, Candidate, Evaluation, Promotion)


@dataclass
class _State:
    evidence: dict[str, EvidenceRecord] = field(default_factory=dict)
    candidates: dict[str, Candidate] = field(default_factory=dict)
    evaluations: dict[str, Evaluation] = field(default_factory=dict)
    promotions: dict[str, Promotion] = field(default_factory=dict)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def _records[T: (EvidenceRecord, Candidate, Evaluation, Promotion)](raw: Any, model: type[T], key_field: str = "id") -> dict[str, T]:
    if type(raw) is not dict:
        raise ValueError("Invalid records")
    result = {}
    required = {item.name for item in fields(model)}
    for key, value in raw.items():
        if type(value) is not dict or set(value) != required:
            raise ValueError("Invalid record fields")
        record = model(**value)
        if getattr(record, key_field) != key or json.loads(json.dumps(asdict(record))) != value:
            raise ValueError("Invalid record")
        result[key] = record
    return result


def _validate_state(state: _State) -> None:
    evaluations: dict[str, list[Evaluation]] = {}
    for evaluation in state.evaluations.values():
        if evaluation.candidate_id not in state.candidates:
            raise ValueError("Missing candidate")
        history = evaluations.setdefault(evaluation.candidate_id, [])
        if history and (history[-1].verdict == "reject" or evaluation.created_at < history[-1].created_at):
            raise ValueError("Invalid evaluation history")
        history.append(evaluation)
    for candidate in state.candidates.values():
        for evidence_id in candidate.evidence_ids:
            evidence = state.evidence.get(evidence_id)
            if evidence is None or evidence.owner_id != candidate.owner_id:
                raise ValueError("Invalid evidence reference")
        history = evaluations.get(candidate.id, [])
        latest = history[-1] if history else None
        promotion = state.promotions.get(candidate.id)
        if promotion is not None:
            if latest is None or latest.id != promotion.evaluation_id or latest.verdict != "promote" or promotion.promoted_at < latest.created_at:
                raise ValueError("Invalid promotion")
            expected = "promoted"
        elif latest is None:
            expected = "proposed"
        else:
            expected = "rejected" if latest.verdict == "reject" else "evaluated"
        if candidate.status != expected:
            raise ValueError("Invalid candidate status")
    if not state.promotions.keys() <= state.candidates.keys():
        raise ValueError("Missing promoted candidate")


class EvidenceStore:
    def __init__(self, storage_dir: str | Path) -> None:
        self._path = Path(storage_dir).resolve() / "evidence.json"
        with _STORE_LOCKS_GUARD:
            self._lock = _STORE_LOCKS.setdefault(os.path.normcase(str(self._path)), RLock())
        self._seen_snapshot = False
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._read()

    def _read(self) -> _State:
        try:
            data = self._path.read_bytes()
        except FileNotFoundError:
            if self._seen_snapshot:
                raise OSError("Evidence store disappeared") from None
            return _State()
        self._seen_snapshot = True
        try:
            raw = json.loads(data, object_pairs_hook=_unique_object)
            if type(raw) is not dict or set(raw) != {"version", "evidence", "candidates", "evaluations", "promotions"} or type(raw["version"]) is not int or raw["version"] != 1:
                raise ValueError("Invalid schema")
            state = _State(
                evidence=_records(raw["evidence"], EvidenceRecord),
                candidates=_records(raw["candidates"], Candidate),
                evaluations=_records(raw["evaluations"], Evaluation),
                promotions=_records(raw["promotions"], Promotion, "candidate_id"),
            )
            _validate_state(state)
            return state
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            raise OSError("Invalid evidence store") from None

    def _write(self, state: _State) -> None:
        _validate_state(state)
        payload = json.dumps({"version": 1, **asdict(state)}, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._path.parent, prefix=".evidence-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            self._seen_snapshot = True
            if os.name != "nt":
                descriptor = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _owned(records: dict[str, _Record], record_id: str, owner_id: str, state: _State) -> _Record:
        record = records.get(record_id)
        if isinstance(record, (Evaluation, Promotion)):
            candidate = state.candidates.get(record.candidate_id)
            record_owner = candidate.owner_id if candidate else None
        else:
            record_owner = record.owner_id if record else None
        if record is None or record_owner != owner_id:
            raise KeyError("Record not found")
        return record

    def add_evidence(self, owner_id: str, kind: EvidenceKind, ref: str, summary: str, *, tags: list[str] | tuple[str, ...] = ()) -> EvidenceRecord:
        validate_text(owner_id, "owner_id")
        with self._lock:
            state = self._read()
            record = EvidenceRecord(id=uuid4().hex, owner_id=owner_id, kind=kind, ref=ref, summary=summary, created_at=time.time(), tags=tags)
            state.evidence[record.id] = record
            self._write(state)
            return record

    def propose_candidate(self, owner_id: str, title: str, evidence_ids: list[str] | tuple[str, ...]) -> Candidate:
        validate_text(owner_id, "owner_id")
        with self._lock:
            state = self._read()
            candidate = Candidate(id=uuid4().hex, owner_id=owner_id, title=title, evidence_ids=evidence_ids)
            for evidence_id in candidate.evidence_ids:
                self._owned(state.evidence, evidence_id, owner_id, state)
            state.candidates[candidate.id] = candidate
            self._write(state)
            return candidate

    def evaluate_candidate(self, candidate_id: str, *, owner_id: str, score: float, verdict: EvaluationVerdict, rationale: str, evaluator: str) -> Evaluation:
        validate_text(owner_id, "owner_id")
        validate_text(candidate_id, "candidate_id")
        with self._lock:
            state = self._read()
            candidate = self._owned(state.candidates, candidate_id, owner_id, state)
            if candidate.status in ("promoted", "rejected"):
                raise ValueError("Candidate is terminal")
            evaluation = Evaluation(id=uuid4().hex, candidate_id=candidate_id, score=score, verdict=verdict, rationale=rationale, evaluator=evaluator, created_at=time.time())
            state.evaluations[evaluation.id] = evaluation
            state.candidates[candidate_id] = replace(candidate, status="rejected" if verdict == "reject" else "evaluated")
            self._write(state)
            return evaluation

    def promote_candidate(self, candidate_id: str, evaluation_id: str, *, owner_id: str) -> Promotion:
        validate_text(owner_id, "owner_id")
        validate_text(candidate_id, "candidate_id")
        validate_text(evaluation_id, "evaluation_id")
        with self._lock:
            state = self._read()
            candidate = self._owned(state.candidates, candidate_id, owner_id, state)
            evaluation = self._owned(state.evaluations, evaluation_id, owner_id, state)
            latest = next((item for item in reversed(state.evaluations.values()) if item.candidate_id == candidate_id), None)
            if candidate.status != "evaluated" or evaluation.candidate_id != candidate_id or evaluation.verdict != "promote" or evaluation != latest:
                raise ValueError("Promotion requires the current promote evaluation for this candidate")
            promotion = Promotion(candidate_id=candidate_id, evaluation_id=evaluation_id, promoted_at=time.time())
            state.promotions[candidate_id] = promotion
            state.candidates[candidate_id] = replace(candidate, status="promoted")
            self._write(state)
            return promotion

    def _get(self, collection: str, record_id: str, owner_id: str) -> Any:
        validate_text(owner_id, "owner_id")
        validate_text(record_id, "record_id")
        with self._lock:
            state = self._read()
            return self._owned(getattr(state, collection), record_id, owner_id, state)

    def _list(self, collection: str, owner_id: str) -> list[Any]:
        validate_text(owner_id, "owner_id")
        with self._lock:
            state = self._read()
            records = getattr(state, collection)
            result = []
            for record_id in records:
                try:
                    result.append(self._owned(records, record_id, owner_id, state))
                except KeyError:
                    continue
            return result

    def get_evidence(self, evidence_id: str, *, owner_id: str) -> EvidenceRecord:
        return self._get("evidence", evidence_id, owner_id)

    def get_candidate(self, candidate_id: str, *, owner_id: str) -> Candidate:
        return self._get("candidates", candidate_id, owner_id)

    def get_evaluation(self, evaluation_id: str, *, owner_id: str) -> Evaluation:
        return self._get("evaluations", evaluation_id, owner_id)

    def get_promotion(self, candidate_id: str, *, owner_id: str) -> Promotion:
        return self._get("promotions", candidate_id, owner_id)

    def list_evidence(self, owner_id: str) -> list[EvidenceRecord]:
        return self._list("evidence", owner_id)

    def list_candidates(self, owner_id: str) -> list[Candidate]:
        return self._list("candidates", owner_id)

    def list_evaluations(self, owner_id: str) -> list[Evaluation]:
        return self._list("evaluations", owner_id)

    def list_promotions(self, owner_id: str) -> list[Promotion]:
        return self._list("promotions", owner_id)
