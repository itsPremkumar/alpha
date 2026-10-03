"""ReasoningBank — a durable, evidence-gated procedure memory.

What it is: a small, offline, deterministic procedure store. It answers two
questions with facts and not vibes: "what worked for something like this
before?" (recall) and "did what we stored actually hold up?" (reinforce).

Design rules (the RSI constraint set):

* **Never self-asserted confidence.** A record's win rate is always
  ``wins / attempts`` over *measured* verdicts. A "success" verdict with no
  evidence reference is demoted to ``"unknown"`` at record time, so the bank
  cannot be talked into trusting a win nobody can point at.
* **Recall is deterministic and bounded.** Keyword/tag/scope overlap,
  documented scoring, a hard ``limit``, and a render that never exceeds
  ``max_chars``. No vector store, no network, no model call.
* **Failures stay visible.** A failed strategy is recallable (so a future
  run can avoid it) unless the caller explicitly passes
  ``include_failures=False``.
* **A broken store degrades, never crashes.** Corrupt JSON loads as empty
  and logs a warning; saves are atomic tmp+replace.
* **Bounded internals.** ``prune()`` caps the record count; reads honor a
  hard limit everywhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_VERDICTS = ("success", "partial", "failure", "unknown")
_DEFAULT_MAX_RECORDS = 500
_MAX_TRIGGER_CHARS = 240
_MAX_STRATEGY_CHARS = 1600
_MAX_EVIDENCE_CHARS = 200


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9_]{3,}", text or "")}


def _fingerprint(scope: str, trigger: str, strategy: str) -> str:
    key = "|".join(
        (
            (scope or "").strip().lower(),
            (trigger or "").strip().lower(),
            (strategy or "").strip().lower(),
        )
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True, slots=True)
class ReasoningRecord:
    strategy_id: str
    scope: str
    trigger: str
    strategy: str
    verdict: str
    attempts: int
    wins: int
    losses: int
    unknowns: int
    evidence_ref: str
    tags: tuple[str, ...]
    fingerprint: str
    created_at: str
    updated_at: str

    @property
    def win_rate(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return self.wins / self.attempts

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "scope": self.scope,
            "trigger": self.trigger,
            "strategy": self.strategy,
            "verdict": self.verdict,
            "attempts": self.attempts,
            "wins": self.wins,
            "losses": self.losses,
            "unknowns": self.unknowns,
            "evidence_ref": self.evidence_ref,
            "tags": list(self.tags),
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReasoningRecord:
        if not isinstance(data, dict):
            raise ValueError("record must be an object")
        tags = data.get("tags") or []
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError("tags must be a list of strings")
        verdict = str(data.get("verdict") or "unknown").strip().lower()
        if verdict not in _VERDICTS:
            raise ValueError(f"unknown verdict {verdict!r}")
        return cls(
            strategy_id=str(data.get("strategy_id") or ""),
            scope=str(data.get("scope") or ""),
            trigger=str(data.get("trigger") or ""),
            strategy=str(data.get("strategy") or ""),
            verdict=verdict,
            attempts=int(data.get("attempts") or 0),
            wins=int(data.get("wins") or 0),
            losses=int(data.get("losses") or 0),
            unknowns=int(data.get("unknowns") or 0),
            evidence_ref=str(data.get("evidence_ref") or ""),
            tags=tuple(tags),
            fingerprint=str(data.get("fingerprint") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


class ReasoningBank:
    """Thread-safe, file-backed procedure memory. Same storage discipline as
    ``ClaimStore``/``ClaimStore``: a ``threading.Lock``, atomic tmp+replace
    saves, and a load failure that logs and starts empty rather than raising."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._records: dict[str, ReasoningRecord] = {}
        self._lock = threading.Lock()
        self._load()

    @property
    def path(self) -> Path:
        return self.storage_path

    # ------------------------------------------------------------------ i/o --

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            records: dict[str, ReasoningRecord] = {}
            skipped = 0
            for item in data.get("records", []):
                try:
                    record = ReasoningRecord.from_dict(item)
                except (ValueError, TypeError, KeyError):
                    skipped += 1
                    continue
                if not record.strategy_id:
                    skipped += 1
                    continue
                records[record.strategy_id] = record
            if skipped:
                logger.warning("reasoning bank load skipped %d malformed record(s)", skipped)
            self._records = records
        except Exception:
            logger.warning("ReasoningBank load failed; starting empty", exc_info=True)
            self._records = {}

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            payload = {"version": 1, "records": [record.to_dict() for record in self._records.values()]}
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("ReasoningBank save failed", exc_info=True)

    # ------------------------------------------------------------- mutation --

    def record(
        self,
        scope: str,
        trigger: str,
        strategy: str,
        *,
        verdict: str,
        evidence_ref: str = "",
        tags: tuple[str, ...] | list[str] | None = None,
    ) -> ReasoningRecord:
        verdict = (verdict or "unknown").strip().lower()
        if verdict not in _VERDICTS:
            verdict = "unknown"
        if verdict == "success" and not (evidence_ref or "").strip():
            verdict = "unknown"
        tags_tuple = tuple(str(t) for t in (tags or ()) if isinstance(t, str) and t)
        trigger = (trigger or "")[:_MAX_TRIGGER_CHARS]
        strategy = (strategy or "")[:_MAX_STRATEGY_CHARS]
        evidence_ref = (evidence_ref or "")[:_MAX_EVIDENCE_CHARS]
        fingerprint = _fingerprint(scope, trigger, strategy)
        now_iso = _now_iso()
        with self._lock:
            existing = self._find_by_fingerprint(scope, trigger, strategy)
            if existing is None:
                record = ReasoningRecord(
                    strategy_id=fingerprint,
                    scope=(scope or "").strip() or "general",
                    trigger=trigger,
                    strategy=strategy,
                    verdict=verdict,
                    attempts=1,
                    wins=1 if verdict == "success" else 0,
                    losses=1 if verdict == "failure" else 0,
                    unknowns=1 if verdict in ("unknown", "partial") else 0,
                    evidence_ref=evidence_ref,
                    tags=tags_tuple,
                    fingerprint=fingerprint,
                    created_at=now_iso,
                    updated_at=now_iso,
                )
                self._records[record.strategy_id] = record
            else:
                attempts = existing.attempts + 1
                wins = existing.wins + (1 if verdict == "success" else 0)
                losses = existing.losses + (1 if verdict == "failure" else 0)
                unknowns = existing.unknowns + (1 if verdict in ("unknown", "partial") else 0)
                record = replace_record(
                    existing,
                    verdict=verdict,
                    attempts=attempts,
                    wins=wins,
                    losses=losses,
                    unknowns=unknowns,
                    evidence_ref=evidence_ref or existing.evidence_ref,
                    tags=sorted(set(existing.tags) | set(tags_tuple)),
                    updated_at=now_iso,
                )
                self._records[record.strategy_id] = record
            result = self._records[record.strategy_id]
        self._save()
        return result

    def reinforce(self, strategy_id: str, verdict: str) -> ReasoningRecord | None:
        """Add one measured outcome to an existing record."""
        verdict = (verdict or "unknown").strip().lower()
        if verdict not in _VERDICTS:
            verdict = "unknown"
        with self._lock:
            existing = self._records.get(strategy_id)
            if existing is None:
                return None
            attempts = existing.attempts + 1
            wins = existing.wins + (1 if verdict == "success" else 0)
            losses = existing.losses + (1 if verdict == "failure" else 0)
            unknowns = existing.unknowns + (1 if verdict in ("unknown", "partial") else 0)
            updated = replace_record(
                existing,
                verdict=verdict,
                attempts=attempts,
                wins=wins,
                losses=losses,
                unknowns=unknowns,
                updated_at=_now_iso(),
            )
            self._records[strategy_id] = updated
            self._save()
            return updated

    # ---------------------------------------------------------------- reads --

    def get(self, strategy_id: str) -> ReasoningRecord | None:
        with self._lock:
            return self._records.get(strategy_id)

    def records(self) -> list[ReasoningRecord]:
        with self._lock:
            return sorted(self._records.values(), key=lambda r: (r.scope, r.trigger))

    def _find_by_fingerprint(self, scope: str, trigger: str, strategy: str) -> ReasoningRecord | None:
        fingerprint = _fingerprint(scope, trigger, strategy)
        for record in self._records.values():
            if record.fingerprint == fingerprint:
                return record
        return None

    def _score_record(self, record: ReasoningRecord, query_tokens: set[str], scope: str | None, tags: tuple[str, ...]) -> int:
        haystack_tokens = _tokenize(record.trigger) | _tokenize(record.strategy) | {t.lower() for t in record.tags} | _tokenize(record.scope)
        overlap = len(query_tokens & haystack_tokens)
        score = overlap * 2
        if scope and record.scope.lower() == scope.lower():
            score += 3
        if tags:
            score += len({t.lower() for t in tags} & {t.lower() for t in record.tags})
        return score

    def recall(
        self,
        query: str,
        *,
        scope: str | None = None,
        tags: tuple[str, ...] | list[str] | None = None,
        limit: int = 5,
        include_failures: bool = True,
    ) -> list[ReasoningRecord]:
        """Deterministic, bounded ranked retrieval.

        Score: scope match ×3, tag overlap ×1, trigger/strategy token
        overlap ×2 each. Records scoring 0 never rank; a failed record
        with no wins is eligible only when ``include_failures`` is true.
        Ties break on win rate, then recency, then trigger text.
        """
        if limit <= 0:
            return []
        query_tokens = _tokenize(query)
        normalized_tags = tuple(str(t) for t in (tags or ()) if isinstance(t, str))
        with self._lock:
            candidates = list(self._records.values())
        scored = [(self._score_record(record, query_tokens, scope, normalized_tags), record) for record in candidates]
        ranked = sorted(
            ((score, record) for score, record in scored if score > 0),
            key=lambda item: (-item[0], -item[1].win_rate, _negate_time(item[1].updated_at), item[1].trigger),
        )
        if not include_failures:
            ranked = [(score, record) for score, record in ranked if not (record.verdict == "failure" and record.wins == 0)]
        return [record for _, record in ranked[:limit]]

    def render(
        self,
        query: str,
        *,
        scope: str | None = None,
        tags: tuple[str, ...] | list[str] | None = None,
        limit: int = 5,
        max_chars: int = 1200,
    ) -> str:
        """Bounded markdown rendering of recalled strategies."""
        hits = self.recall(query, scope=scope, tags=tags, limit=limit)
        if not hits:
            return ""
        lines = []
        used = 0
        for record in hits:
            line = f"- **{record.scope}** {record.trigger}: {record.strategy} *(measured: {record.wins}/{record.attempts} wins)*"
            if used + len(line) > max_chars:
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    def prune(self, *, max_records: int = _DEFAULT_MAX_RECORDS) -> int:
        """Keep at most ``max_records``; evict losing, then oldest."""
        if max_records <= 0:
            return 0
        with self._lock:
            records = list(self._records.values())
            if len(records) <= max_records:
                return 0
            records.sort(key=lambda r: (-r.win_rate, -r.attempts, r.updated_at))
            keep = records[:max_records]
            removed = len(records) - len(keep)
            self._records = {record.strategy_id: record for record in keep}
        self._save()
        return removed


def _negate_time(timestamp: str) -> float:
    try:
        return -time.mktime(time.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return float("inf")


def replace_record(record: ReasoningRecord, **changes: Any) -> ReasoningRecord:
    values = record.to_dict()
    values["tags"] = tuple(values.get("tags") or ())
    values.update(changes)
    return ReasoningRecord(**values)


def _default_storage_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "reasoning_bank" / "records.json"
    except Exception:
        return Path.cwd() / ".alpha" / "reasoning_bank" / "records.json"


_bank: ReasoningBank | None = None
_bank_path: str | None = None
_bank_lock = threading.Lock()


def get_reasoning_bank(storage_path: str | Path | None = None) -> ReasoningBank:
    """Process-wide ReasoningBank singleton; rebuilds when ``runtime_home()`` moves."""
    global _bank, _bank_path
    with _bank_lock:
        if storage_path is not None:
            _bank = ReasoningBank(storage_path)
            try:
                _bank_path = str(_bank.storage_path)
            except Exception:
                _bank_path = None
            return _bank
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _bank is None or _bank_path != live:
            _bank = ReasoningBank()
            try:
                _bank_path = str(_bank.storage_path.resolve())
            except Exception:
                _bank_path = live
        return _bank
