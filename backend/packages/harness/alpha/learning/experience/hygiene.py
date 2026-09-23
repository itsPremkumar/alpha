"""Experience hygiene: background maintenance over the ExperienceStore.

Disclosed maintenance rules (every run restates them in ``HygieneReport.notes``):

- **Dedup / merge** — records of the same kind whose normalized statement text
  (lowercased, punctuation stripped, whitespace collapsed) is equal, or whose
  token-set Jaccard overlap is ``>= DEDUP_THRESHOLD`` (0.9, tokens lowercased
  with a small stopword list removed), are merged greedily: the earliest record
  survives, ``success_count``/``failure_count``/``reuse_count`` are summed from
  the real counts of both sides, evidence/tags/lessons/pitfalls are unioned,
  and the duplicate is removed. The survivor gets an in-record ``dedup_merge``
  audit entry naming the merged id, method, and overlap.
- **Expiry** — records whose ``expires_at`` has passed are removed (checked
  before dedup so stale counts never merge into fresh records).
- **Down-rank** — records with real failures get
  ``confidence := round(min(current, (success_count + 1) / (success_count + failure_count + 2)), 4)``.
  The formula is a Laplace-smoothed empirical success rate over REAL
  ``success_count``/``failure_count`` arithmetic (it equals the neutral 0.5
  baseline at 0/0 and can only lower confidence — hygiene never inflates it).
  The full formula with the concrete counts is written to the record's
  ``hygiene_note`` field and to its audit entry.
- **Review refresh** — ``last_reviewed`` is stamped when missing or older than
  ``REVIEW_STALE_AFTER_SECONDS``, with an audit entry, so revalidation cadence
  is observable.

No rewrite is silent: every mutation appends an audit entry to the surviving
record's ``audit`` list and to ``HygieneReport.audit``; disk-backed stores
additionally get an append-only ``<storage>.hygiene_audit.jsonl`` sidecar.

Entry point: ``run_hygiene(store) -> HygieneReport`` (tests stub/seed stores
directly and call this seam).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from alpha.learning.experience.models import ExperienceKind, ExperienceRecord
from alpha.learning.experience.store import ExperienceStore

# Disclosed dedup threshold: token-set Jaccard overlap >= this value marks two
# same-kind statements as near-equivalent (normalized-text equality counts too).
DEDUP_THRESHOLD = 0.9

# Disclosed revalidation cadence for `last_reviewed`.
REVIEW_STALE_AFTER_SECONDS = 7 * 24 * 3600

# Disclosed down-rank formula (Laplace-smoothed success rate over real counts).
DOWNSHIFT_FORMULA = "(success_count + 1) / (success_count + failure_count + 2)"

_MERGE_LIST_FIELDS = (
    "evidence",
    "tags",
    "lessons_learned",
    "pitfalls_to_avoid",
    "error_types",
    "modified_files",
)

_STOPWORDS = {"the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "is", "are", "this", "that", "with"}


def _normalized(text: str) -> str:
    """Lowercased punctuation-stripped form used for equality dedup."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _tokens(text: str) -> set[str]:
    return {tok for tok in _normalized(text).split() if tok not in _STOPWORDS}


def _entry(run_id: str, action: str, target_id: str, detail: dict[str, Any]) -> dict[str, Any]:
    return {"ts": time.time(), "run_id": run_id, "action": action, "target_id": target_id, "detail": dict(detail)}


@dataclass
class HygieneReport:
    """Result of one ``run_hygiene`` pass: counts, disclosures, and full audit."""

    run_id: str
    started_at: float
    scanned: int = 0
    finished_at: float = 0.0
    expired_ids: list[str] = field(default_factory=list)
    merged: list[dict[str, Any]] = field(default_factory=list)
    downranked: list[dict[str, Any]] = field(default_factory=list)
    refreshed_ids: list[str] = field(default_factory=list)
    persist_refusals: list[dict[str, Any]] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sidecar_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _persist(store: ExperienceStore, record: ExperienceRecord, run_id: str, action: str, report: HygieneReport) -> bool:
    """Persist a mutated record; surface (never hide) a store refusal."""
    result = store.record(record)
    if result.get("stored"):
        return True
    reason = str(result.get("reason", "unknown refusal"))
    report.persist_refusals.append({"experience_id": record.experience_id, "action": action, "reason": reason})
    report.audit.append(
        _entry(
            run_id,
            "persist_refused",
            record.experience_id,
            {"action_attempted": action, "reason": reason, "note": "mutation applied in memory only; the store refused the write"},
        )
    )
    return False


def _merge_into(
    store: ExperienceStore,
    survivor: ExperienceRecord,
    loser: ExperienceRecord,
    method: str,
    overlap: float,
    run_id: str,
    report: HygieneReport,
) -> bool:
    """Merge ``loser`` into ``survivor`` (summing real counts); roll back if the store refuses the write."""
    backup = {
        "success_count": survivor.success_count,
        "failure_count": survivor.failure_count,
        "reuse_count": survivor.reuse_count,
        "expires_at": survivor.expires_at,
        "audit": list(survivor.audit),
        **{name: list(getattr(survivor, name)) for name in _MERGE_LIST_FIELDS},
    }

    survivor.success_count += loser.success_count
    survivor.failure_count += loser.failure_count
    survivor.reuse_count += loser.reuse_count
    for name in _MERGE_LIST_FIELDS:
        merged_list = getattr(survivor, name)
        for item in getattr(loser, name):
            if item not in merged_list:
                merged_list.append(item)
    if loser.expires_at is not None:
        survivor.expires_at = loser.expires_at if survivor.expires_at is None else min(survivor.expires_at, loser.expires_at)

    detail = {
        "merged_id": loser.experience_id,
        "method": method,
        "overlap": round(overlap, 4),
        "threshold": DEDUP_THRESHOLD,
        "merged_counts": {
            "success_count": loser.success_count,
            "failure_count": loser.failure_count,
            "reuse_count": loser.reuse_count,
        },
    }
    survivor.append_audit("dedup_merge", {**detail, "run_id": run_id})

    if not _persist(store, survivor, run_id, "dedup_merge", report):
        # Restore every touched field so nothing is half-merged or double-counted.
        survivor.success_count = backup["success_count"]
        survivor.failure_count = backup["failure_count"]
        survivor.reuse_count = backup["reuse_count"]
        survivor.expires_at = backup["expires_at"]
        survivor.audit = backup["audit"]
        for name in _MERGE_LIST_FIELDS:
            setattr(survivor, name, backup[name])
        return False

    store.remove(loser.experience_id)
    report.merged.append({"survivor_id": survivor.experience_id, **detail})
    report.audit.append(_entry(run_id, "dedup_merge", survivor.experience_id, detail))
    return True


def run_hygiene(store: ExperienceStore) -> HygieneReport:
    """Run one maintenance pass over ``store`` and return the audited report.

    Order: expiry -> dedup/merge -> down-rank -> review refresh (expiry first so
    stale records never merge their counts into fresh ones).
    """
    run_id = str(uuid.uuid4())
    now = time.time()
    report = HygieneReport(run_id=run_id, started_at=now)
    report.scanned = len(store.list_all())
    report.notes = [
        "order: expiry -> dedup/merge -> down-rank -> review refresh",
        (f"dedup: same-kind statements merged on normalized-text equality or token-set Jaccard overlap >= {DEDUP_THRESHOLD} (tokens lowercased, stopword-stripped; greedy first-match)"),
        "merge: success_count/failure_count/reuse_count summed from the real counts of both records; earliest record survives",
        "expiry: records with expires_at <= now are removed before dedup",
        (f"down-rank: confidence := round(min(current, {DOWNSHIFT_FORMULA}), 4) when failure_count >= 1; Laplace-smoothed rate over real success/failure counts, neutral 0.5 at 0/0, never raises confidence"),
        f"review refresh: last_reviewed stamped when missing or older than {REVIEW_STALE_AFTER_SECONDS}s",
        "audit: every mutation appends to the surviving record's audit list and report.audit; disk stores also get a *.hygiene_audit.jsonl sidecar",
    ]

    # 1. Expiry ---------------------------------------------------------------
    for rec in list(store.list_all()):
        if not rec.is_expired(now):
            continue
        detail = {"expires_at": rec.expires_at, "now": now, "kind": rec.kind.value}
        if store.remove(rec.experience_id):
            report.expired_ids.append(rec.experience_id)
            report.audit.append(_entry(run_id, "expire", rec.experience_id, detail))

    # 2. Dedup / merge --------------------------------------------------------
    by_kind: dict[ExperienceKind, list[tuple[ExperienceRecord, str]]] = {}
    for rec in store.list_all():
        text = _normalized(rec.statement or rec.task_goal)
        if text:
            by_kind.setdefault(rec.kind, []).append((rec, text))

    for kind_records in by_kind.values():
        kept: list[tuple[ExperienceRecord, set[str], str]] = []
        for rec, norm in sorted(kind_records, key=lambda pair: (pair[0].timestamp, pair[0].experience_id)):
            rec_tokens = _tokens(norm)
            target: ExperienceRecord | None = None
            method = ""
            overlap = 0.0
            for kept_rec, kept_tokens, kept_norm in kept:
                if norm == kept_norm:
                    target, method, overlap = kept_rec, "normalized_equality", 1.0
                    break
                union = rec_tokens | kept_tokens
                if union:
                    score = len(rec_tokens & kept_tokens) / len(union)
                    if score >= DEDUP_THRESHOLD:
                        target, method, overlap = kept_rec, f"jaccard>={DEDUP_THRESHOLD}", score
                        break
            if target is None:
                kept.append((rec, rec_tokens, norm))
                continue
            if not _merge_into(store, target, rec, method, overlap, run_id, report):
                # Persist refused: keep the record as its own canonical entry.
                kept.append((rec, rec_tokens, norm))

    # 3. Down-rank repeated failures (real arithmetic, formula disclosed) -----
    for rec in store.list_all():
        if rec.failure_count < 1:
            continue
        successes, failures = rec.success_count, rec.failure_count
        laplace = (successes + 1) / (successes + failures + 2)
        new_confidence = round(min(rec.confidence, laplace), 4)
        if new_confidence >= rec.confidence:
            continue
        old_confidence = rec.confidence
        rec.confidence = new_confidence
        detail = {
            "old_confidence": old_confidence,
            "new_confidence": new_confidence,
            "success_count": successes,
            "failure_count": failures,
            "formula": DOWNSHIFT_FORMULA,
            "computed": f"({successes} + 1) / ({successes} + {failures} + 2) = {laplace:.4f}",
            "rule": "new_confidence = round(min(current_confidence, formula), 4); hygiene never raises confidence",
        }
        rec.hygiene_note = (
            f"run_hygiene down-rank: confidence = min({old_confidence}, {DOWNSHIFT_FORMULA}) "
            f"= min({old_confidence}, ({successes} + 1) / ({successes} + {failures} + 2)) "
            f"= {new_confidence}; real counts success_count={successes} failure_count={failures}"
        )
        rec.append_audit("downrank", {**detail, "run_id": run_id})
        if _persist(store, rec, run_id, "downrank", report):
            report.downranked.append({"experience_id": rec.experience_id, **detail})
            report.audit.append(_entry(run_id, "downrank", rec.experience_id, detail))

    # 4. Review refresh -------------------------------------------------------
    for rec in store.list_all():
        if rec.last_reviewed is not None and now - rec.last_reviewed < REVIEW_STALE_AFTER_SECONDS:
            continue
        previous = rec.last_reviewed
        rec.last_reviewed = now
        detail = {"previous_last_reviewed": previous, "interval_seconds": REVIEW_STALE_AFTER_SECONDS}
        rec.append_audit("refresh_reviewed", {**detail, "run_id": run_id})
        if _persist(store, rec, run_id, "refresh_reviewed", report):
            report.refreshed_ids.append(rec.experience_id)
            report.audit.append(_entry(run_id, "refresh_reviewed", rec.experience_id, detail))

    report.finished_at = time.time()

    # Sidecar: append-only audit trail next to the store file (when on disk).
    if report.audit and store.storage_path:
        sidecar = Path(str(store.storage_path) + ".hygiene_audit.jsonl")
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        with open(sidecar, "a", encoding="utf-8") as fh:
            for entry in report.audit:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        report.sidecar_path = str(sidecar)

    return report
