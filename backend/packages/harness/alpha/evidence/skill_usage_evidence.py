"""Honest skill-usage evidence bridge: real telemetry -> EvidenceStore payloads.

Two builders, both honesty-pinned:

- :func:`build_usage_evidence` returns the kwargs payload for
  :meth:`alpha.evidence.store.EvidenceStore.add_evidence`
  (``kind="observation"``, ``ref="skill-usage://<name>"`). The summary is
  computed ONLY from real tracker-row fields: ``uses``, the ``last_used_at``
  age in days to 2 decimals, and ``created_by``. No telemetry row for the
  skill yields an explicit honest-unknown record
  (``summary="usage unknown: no telemetry row for <name>"``,
  ``tags=("honesty:unknown",)``) — never zeros-as-fact.

- :func:`build_promotion_readiness` reads an evolution-engine record's REAL
  ``checks_passed``/``checks_total`` (importing the engine's validation-kind
  constants — never re-declaring them) and refuses (``ready=False`` with a
  reason mentioning "structural-only") whenever runtime behavior was not
  verified, per ``alpha.skills.evolution_engine``'s honesty contract.
  ``evolution_engine.py`` is imported, never edited.

Scope: this bridge speaks only to evaluation/runtime-verification evidence.
Moderation and approval policy remain the engine's own gate.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from alpha.skills.evolution_engine import VALIDATION_STRUCTURAL_AND_SUITE, VALIDATION_STRUCTURAL_ONLY

__all__ = [
    "build_promotion_readiness",
    "build_usage_evidence",
]

_SECONDS_PER_DAY = 86400.0
_USAGE_REF_PREFIX = "skill-usage://"
_HONEST_UNKNOWN_TAG = "honesty:unknown"


def _int_or_none(value: Any) -> int | None:
    """Real ints only (``bool`` is rejected); anything else is honestly None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def build_usage_evidence(tracker: Any, skill_name: str, *, owner_id: str, now: float | None = None) -> dict[str, Any]:
    """Payload for ``EvidenceStore.add_evidence(...)`` from real tracker rows.

    ``tracker`` is a :class:`alpha.skills.usage.SkillUsageTracker`; ``now``
    defaults to the wall clock and exists so age arithmetic is testable.
    """
    row = tracker.stats(skill_name)
    if row is None:
        return {
            "owner_id": owner_id,
            "kind": "observation",
            "ref": f"{_USAGE_REF_PREFIX}{skill_name}",
            "summary": f"usage unknown: no telemetry row for {skill_name}",
            "tags": (_HONEST_UNKNOWN_TAG,),
        }
    uses = int(row.uses)
    if row.last_used_at is None:
        last_used = "last used: never recorded"
    else:
        moment = time.time() if now is None else float(now)
        age_days = round((moment - float(row.last_used_at)) / _SECONDS_PER_DAY, 2)
        last_used = f"last used {age_days:.2f} days ago"
    summary = f"skill '{skill_name}' usage: {uses} recorded use(s); {last_used}; created_by {row.created_by}"
    return {
        "owner_id": owner_id,
        "kind": "observation",
        "ref": f"{_USAGE_REF_PREFIX}{skill_name}",
        "summary": summary,
        "tags": (),
    }


def build_promotion_readiness(record: Any) -> dict[str, Any]:
    """Honest readiness verdict from an evolution-engine record's REAL checks.

    ``record`` is a ``SkillEvolutionProposal`` (or its ``to_dict()``
    mapping). Refusal order: no evaluation -> evaluation error ->
    ``structural-only`` validation (runtime behavior was NOT verified —
    refused EVEN when every structural check passed) -> missing/zero checks
    -> failing checks -> an unverified validation kind. Only a record whose
    declared suite actually ran with all checks passing is ``ready=True``,
    and the reason states the suite's scope honestly.
    """
    if isinstance(record, Mapping):
        evaluation = record.get("evaluation")
    else:
        evaluation = getattr(record, "evaluation", None)
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    passed = _int_or_none(evaluation.get("checks_passed"))
    total = _int_or_none(evaluation.get("checks_total"))
    kind = evaluation.get("validation_kind")
    base: dict[str, Any] = {
        "checks_passed": passed,
        "checks_total": total,
        "validation_kind": kind,
    }
    if not evaluation:
        return {
            "ready": False,
            "reason": "no evaluation evidence recorded; promotion readiness cannot be claimed without it.",
            **base,
        }
    error = evaluation.get("error")
    if error:
        return {
            "ready": False,
            "reason": f"evaluation errored ({error}); nothing was verified at runtime.",
            **base,
        }
    if kind == VALIDATION_STRUCTURAL_ONLY:
        counts = f" (checks {passed}/{total})" if passed is not None and total is not None else ""
        return {
            "ready": False,
            "reason": f"not ready: validation was structural-only{counts}; runtime behavior was NOT verified.",
            **base,
        }
    if passed is None or total is None or total < 1:
        return {
            "ready": False,
            "reason": "no evaluation checks recorded; readiness cannot be claimed without recorded checks.",
            **base,
        }
    if passed != total:
        return {
            "ready": False,
            "reason": f"not ready: checks did not all pass ({passed}/{total}); {total - passed} recorded check(s) failed.",
            **base,
        }
    if kind != VALIDATION_STRUCTURAL_AND_SUITE:
        return {
            "ready": False,
            "reason": f"not ready: validation kind {kind!r} does not record runtime verification.",
            **base,
        }
    return {
        "ready": True,
        "reason": f"checks {passed}/{total} passed and the declared test suite ran (validation_kind={kind}); runtime behavior was verified only for that declared suite.",
        **base,
    }
