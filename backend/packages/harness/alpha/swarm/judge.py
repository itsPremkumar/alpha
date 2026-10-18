"""Rubric acceptance judge for swarm plans (bot-mode plan phase 3.3).

The deterministic per-task gate (``verify_acceptance_criteria`` plus claim
grounding) answers "did the declared mechanical check pass".  It cannot answer
the non-mechanical half of acceptance — *does this deliverable actually meet the
goal* — and the old answer to that question was a 40% keyword-overlap number
written into ``plan.quality_score``.  This module replaces that number with a
rubric judgement over the goal and the per-task evidence, under the same
honesty rule every other measurement in the package follows:

- the report is ``{verdict, score, reasons, evidence_refs, model, ...}`` and
  ``score`` is ``None`` whenever nothing was measured — never a reassuring 0.0;
- **judge failure means ``unverified``, never a pass**: an empty reply,
  non-JSON output, an unknown verdict, a ``passed`` with no measurable score, a
  raising caller, or no ``invoke`` callable at all all funnel through
  :func:`unverified_report` and cannot be spelled any other way;
- the caller supplies the model round-trip (``invoke``), because
  ``SwarmAggregator.aggregate`` is synchronous and may run under the
  coordinator's state lock — this module must never open a model call itself
  (event-loop discipline, backend/AGENTS.md);
- the ``model`` label comes from the caller, never from the model's own output:
  a model cannot attest to which model it is.

The judge is opt-in per plan: ``SwarmPlan.acceptance_mode`` defaults to
``"evidence"`` (deterministic and free), so a plan that never asked for a judge
spends nothing — the default *is* the cost gate, which is why plan §11.3 needs
no judge flag.  Tests: ``backend/tests/test_swarm_judge.py``.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing-only, keeps this module stdlib-only
    from alpha.swarm.models import SwarmPlan

logger = logging.getLogger(__name__)

# -- Vocabulary ------------------------------------------------------------
# Single authority for both the acceptance-mode names (``models.py`` imports
# them, so the persisted field and this module cannot drift) and the verdict
# names the aggregator and the recovery surfaces key on.

ACCEPTANCE_MODES: tuple[str, ...] = ("evidence", "judge", "both")
DEFAULT_ACCEPTANCE_MODE = "evidence"

VERDICT_PASSED = "passed"
VERDICT_UNVERIFIED = "unverified"
VERDICT_FAILED = "failed"
VERDICTS: tuple[str, ...] = (VERDICT_PASSED, VERDICT_UNVERIFIED, VERDICT_FAILED)

# Identifies which judge produced a report so a renderer never mixes bases
# (same discipline as ``claim_grounding``'s GROUNDING_BASIS).
JUDGE_BASIS = "rubric_judge_v1"

# (system, user) -> raw model text.  The blocking decision belongs to the
# wiring layer, never to this module.
JudgeInvoke = Callable[[str, str], str]

# Bounds.  A judge prompt is built from unbounded worker output, so every
# slice is capped and the task list discloses its truncation instead of
# silently shortening the record the verdict rests on.
_MAX_GOAL_CHARS = 2_000
_MAX_TASKS = 32
_MAX_TASK_OBJECTIVE_CHARS = 400
_MAX_RESULT_CHARS = 600
_MAX_CRITERIA = 8
_MAX_CRITERION_CHARS = 200
_MAX_EVIDENCE_PER_TASK = 4
_MAX_ARTIFACTS_PER_TASK = 8
_MAX_RESPONSE_CHARS = 16_000
_MAX_REASONS = 8
_MAX_REASON_CHARS = 300
_MAX_REFS = 16
_MAX_REF_CHARS = 160
_MAX_ERROR_CHARS = 200


def normalize_acceptance_mode(mode: Any) -> str:
    """Tolerant read for a persisted or caller-supplied acceptance mode.

    Same shape as the ``SwarmMode`` read in ``SwarmPlan.from_dict``: an unknown
    spelling resolves to the default *free* mode, never to an implicit opt-in
    that could start spending tokens.
    """

    return mode if isinstance(mode, str) and mode in ACCEPTANCE_MODES else DEFAULT_ACCEPTANCE_MODE


def judge_required(mode: Any) -> bool:
    """True only for the two modes that actually asked for a model judgement."""

    return normalize_acceptance_mode(mode) in ("judge", "both")


def unverified_report(reason: str, *, model: str | None = None, error: str | None = None) -> dict[str, Any]:
    """The single constructor for "the judge did not decide".

    Every failure path in this module funnels here, so a failure can never be
    reported as anything else: ``score`` stays ``None`` (nothing was measured)
    and the reason names exactly what was missing.
    """

    return {
        "verdict": VERDICT_UNVERIFIED,
        "score": None,
        "reasons": [_clip(str(reason), _MAX_REASON_CHARS)],
        "evidence_refs": [],
        "model": model,
        "basis": JUDGE_BASIS,
        "error": (_clip(str(error), _MAX_ERROR_CHARS) if error else None),
    }


def build_judge_prompt(plan: SwarmPlan) -> dict[str, str]:
    """Build the bounded rubric prompt: rubric in the system message, the
    plan's record in the user message, truncation disclosed rather than
    silent."""

    system = (
        "You are the acceptance judge for a multi-agent swarm plan. "
        "Judge ONLY from the record you are given: you have no tools and no workspace access.\n"
        "Rubric:\n"
        f'- "{VERDICT_PASSED}" — the recorded evidence supports that the goal was achieved.\n'
        f'- "{VERDICT_UNVERIFIED}" — the record is missing, incomplete, or cannot establish the outcome. '
        "Pick this whenever the evidence is ambiguous; an unsupported pass is worse than no verdict.\n"
        f'- "{VERDICT_FAILED}" — the record contradicts the goal or reports failure.\n'
        "Respond with ONLY one JSON object, no prose and no code fences:\n"
        '{"verdict": "passed|unverified|failed", "score": <0.0-1.0 or null>, '
        '"reasons": ["<short reason>", ...], "evidence_refs": ["<task id or artifact>", ...]}\n'
        "- score: your 0..1 quality estimate for the deliverable; null when it cannot be estimated.\n"
        "- reasons: 1-5 short strings naming what you relied on.\n"
        f"- evidence_refs: at most {_MAX_REFS} task ids or artifact names the verdict rests on."
    )

    tasks = list(plan.tasks.values())
    lines = [
        f"Goal: {_clip(str(plan.goal or ''), _MAX_GOAL_CHARS)}",
        f"Declared acceptance mode: {normalize_acceptance_mode(getattr(plan, 'acceptance_mode', None))}",
        "",
        "Recorded tasks and evidence:",
    ]
    shown = 0
    omitted = 0
    for task in tasks:
        if shown >= _MAX_TASKS:
            omitted += 1
            continue
        lines.extend(_task_block(task))
        shown += 1
    if omitted:
        lines.append(f"- ({omitted} further task(s) omitted at the {_MAX_TASKS}-task bound)")
    if not tasks:
        lines.append("- (no tasks executed; nothing was delivered)")
    return {"system": system, "user": "\n".join(lines)}


def parse_judge_response(text: Any, *, model: str | None = None) -> dict[str, Any]:
    """Parse the judge's raw text into the report shape.

    Nothing reachable from here can produce a pass out of bad input: an empty
    reply, non-JSON output, an unknown verdict, or a ``passed`` carrying no
    measurable score all come back as ``unverified`` with ``score=None`` and a
    reason naming the defect.
    """

    if not isinstance(text, str) or not text.strip():
        return unverified_report("judge_empty_response", model=model)
    payload = _extract_json(text)
    if not isinstance(payload, dict):
        return unverified_report("judge_malformed_output", model=model)
    verdict = str(payload.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        return unverified_report("judge_unknown_verdict", model=model, error=verdict or "<missing>")
    score = _coerce_score(payload.get("score"))
    if verdict == VERDICT_PASSED and score is None:
        # A pass the judge could not attach a measured score to is exactly the
        # fabricated success this module exists to prevent.
        return unverified_report("judge_score_missing", model=model)
    return {
        "verdict": verdict,
        "score": score,
        "reasons": _string_list(payload.get("reasons"), _MAX_REASONS, _MAX_REASON_CHARS),
        "evidence_refs": _string_list(payload.get("evidence_refs"), _MAX_REFS, _MAX_REF_CHARS),
        "model": model,
        "basis": JUDGE_BASIS,
        "error": None,
    }


def run_acceptance_judge(
    plan: SwarmPlan,
    *,
    invoke: JudgeInvoke | None,
    model: str | None = None,
) -> dict[str, Any]:
    """Run the judge through the caller-supplied model round-trip.

    ``invoke(system, user) -> str`` is deliberately provided from outside: the
    aggregator is synchronous and may hold the coordinator's state lock, so the
    blocking call (and where it runs) belongs to the wiring layer.  With no
    ``invoke`` there is no judgement — the report says ``judge_not_configured``
    rather than inventing a verdict.
    """

    if invoke is None:
        return unverified_report("judge_not_configured", model=model)
    try:
        prompt = build_judge_prompt(plan)
        raw = invoke(prompt["system"], prompt["user"])
    except Exception as exc:  # a failing judge is unverified, never a pass
        logger.warning(
            "swarm %s acceptance judge failed: %s",
            getattr(plan, "swarm_id", "?"),
            exc,
            exc_info=True,
        )
        return unverified_report("judge_invocation_failed", model=model, error=f"{type(exc).__name__}: {exc}")
    return parse_judge_response(raw, model=model)


# -- Internal helpers ------------------------------------------------------


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def _extract_json(text: str) -> Any:
    """Pull the first JSON object out of a model reply, tolerating fences and
    surrounding prose.  Returns ``None`` when nothing parses — the caller
    turns that into ``unverified``, never into a guess."""

    candidate = text[:_MAX_RESPONSE_CHARS].strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
    return None


def _coerce_score(value: Any) -> float | None:
    # ``bool`` is an ``int`` in Python; ``True`` is not a quality score.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    # Out-of-range values are refused, never clamped: a bound the judge broke
    # is a defect to disclose, not a number to smooth over.
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        return None
    return result


def _string_list(value: Any, limit: int, char_limit: int) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    out: list[str] = []
    for item in value:
        if len(out) >= limit:
            break
        text = _clip(str(item).strip(), char_limit)
        if text:
            out.append(text)
    return out


def _criterion_text(criterion: Any) -> str:
    if isinstance(criterion, Mapping):
        for key in ("description", "kind", "type", "value", "text"):
            value = criterion.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        try:
            return json.dumps(criterion, default=str)
        except (TypeError, ValueError):
            return str(criterion)
    return str(criterion)


def _evidence_ref(item: Any) -> str:
    if isinstance(item, Mapping):
        for key in ("source", "tool", "type", "id", "ref", "url"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return _clip(value.strip(), _MAX_REF_CHARS)
        try:
            return _clip(json.dumps(item, default=str), _MAX_REF_CHARS)
        except (TypeError, ValueError):
            return _clip(str(item), _MAX_REF_CHARS)
    return _clip(str(item), _MAX_REF_CHARS)


def _task_block(task: Any) -> list[str]:
    state = getattr(task, "state", None)
    state_label = str(getattr(state, "value", state) or "unknown")
    lines = [
        f"- task {getattr(task, 'task_id', '?')} [{state_label}] acceptance={getattr(task, 'acceptance_status', 'not_required')}",
        f"  objective: {_clip(str(getattr(task, 'objective', '') or ''), _MAX_TASK_OBJECTIVE_CHARS)}",
    ]
    summary = str(getattr(task, "result_summary", "") or "")
    if summary:
        lines.append(f"  result: {_clip(summary, _MAX_RESULT_CHARS)}")
    criteria = list(getattr(task, "acceptance_criteria", ()) or ())[:_MAX_CRITERIA]
    if criteria:
        lines.append("  acceptance criteria: " + "; ".join(_clip(_criterion_text(c), _MAX_CRITERION_CHARS) for c in criteria))
    evidence = list(getattr(task, "evidence", ()) or ())[:_MAX_EVIDENCE_PER_TASK]
    if evidence:
        lines.append("  evidence: " + "; ".join(_evidence_ref(item) for item in evidence))
    artifacts = list(getattr(task, "output_artifacts", ()) or ())
    if artifacts:
        lines.append("  artifacts: " + ", ".join(_clip(str(a), _MAX_REF_CHARS) for a in artifacts[:_MAX_ARTIFACTS_PER_TASK]))
    grounding = getattr(task, "claim_grounding", None)
    if isinstance(grounding, Mapping):
        unresolved = grounding.get("unresolved")
        if isinstance(unresolved, int) and not isinstance(unresolved, bool):
            lines.append(f"  claim grounding unresolved: {unresolved}")
    return lines
