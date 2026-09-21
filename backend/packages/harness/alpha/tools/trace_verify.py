"""Semantic verification of a tool-call trace (System One fast path).

``receipt_verification`` answers a *syntactic* question: does the cited ``[rN]``
exist in the execution record, and did it succeed? That is deliberately pure —
no IO, no model calls — and it cannot tell whether the trace makes sense.

This module adds the semantic layer on top, porting TypeSafe's trace-check
decomposition. It asks one System One request carrying a handful of atomic
questions about the whole trace:

============================  ============================================================
question                      what it catches
============================  ============================================================
``wrong_tool``                a step used a tool that does not fit its purpose
``args_mismatch``             arguments disagree with what the step was trying to do
``unused_output``             a step's result is never consumed by anything
``cross_step_disagree``       dates / units / ids / parameters contradict across steps
``conclusion_unsupported``    the final answer does not follow from the results
``severity``                  ordered rubric for how bad the worst problem is
============================  ============================================================

Two-stage by design. Stage one is one request over the whole trace. Only if
something fires does stage two run a *localisation* pass that asks, per step,
which check failed — so the common (clean) case costs exactly one call, and the
rare (dirty) case tells you where to look.

Contract, like every System One site in Alpha:

* ``None`` means "no signal, use the existing layer" — never "the trace is bad".
* Deterministic problems (error status, empty output) are reported with or
  without System One, because they need no model.
* Advisory only. Nothing here blocks a run.

Vocabulary: the boolean is ``trace_supported``, never ``verified``/``passed``
— strong-positive words belong to the runtime hard gate.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from alpha.config.system_one_config import RiskTier
from alpha.models.system_one import (
    BooleanQuestion,
    ScoreQuestion,
    SystemOneClient,
    get_system_one_client,
)

logger = logging.getLogger(__name__)

#: Call-site label recorded in the System One decision log (see evaluation/system_one_calibration.py).
SITE = "trace"

VERDICT_SOURCE = "tool_trace"
VERDICT_REQUIREMENT = "trace_supports_answer"

#: How many steps go into the state. The state budget is 32K tokens, but long
#: traces dilute the signal; the most recent steps are the ones that matter for
#: "does the conclusion follow".
STEP_CAP = 12

#: Per-step localisation is capped so a pathological trace cannot fan out into
#: hundreds of questions.
LOCALISE_CAP = 8

#: Excerpt sizes. Enough to see what happened, small enough to stay in budget.
_ARG_CHARS = 400
_RESULT_CHARS = 600

#: A boolean above this is read as "yes, this problem is present". Booleans are
#: calibrated, so 0.5 is the decision boundary; confidence (distance from 0.5)
#: is reported separately rather than used to suppress the flag.
FLAG_AT = 0.5

SEVERITY_LEVELS = [
    "No problem — the trace is coherent and the answer follows from it.",
    "Minor — cosmetic or redundant steps, conclusion still supported.",
    "Moderate — a step's output is unused or arguments look stale, conclusion still plausible.",
    "Serious — a wrong tool, a contradiction between steps, or an unsupported conclusion.",
]


@dataclass
class TraceStep:
    """One executed step of a trace."""

    tool: str
    args: str = ""
    result: str = ""
    status: str = "success"
    step_id: str = ""
    intent: str = ""

    def to_state(self) -> dict[str, str]:
        return {
            "id": self.step_id,
            "intent": self.intent[:200],
            "tool": self.tool,
            "args": self.args[:_ARG_CHARS],
            "status": self.status,
            "result": self.result[:_RESULT_CHARS],
        }


@dataclass
class TraceVerdict:
    """Advisory verdict on whether a trace supports its answer."""

    source: str = VERDICT_SOURCE
    requirement: str = VERDICT_REQUIREMENT
    trace_supported: bool | None = None
    problems: list[str] = field(default_factory=list)
    severity: float | None = None
    confidence: float | None = None
    jev_used: bool = False
    localized: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "requirement": self.requirement,
            "trace_supported": self.trace_supported,
            "problems": list(self.problems),
            "severity": self.severity,
            "confidence": self.confidence,
            "jev_used": self.jev_used,
            "localized": list(self.localized),
        }


# --------------------------------------------------------------------------
# Deterministic pass — no model, always available
# --------------------------------------------------------------------------


def deterministic_problems(steps: list[TraceStep]) -> list[str]:
    """Problems that need no judgement: error status, or success with nothing."""
    problems: list[str] = []
    for index, step in enumerate(steps):
        label = step.step_id or f"step {index}"
        status = (step.status or "").lower()
        if status in {"error", "failed", "failure"}:
            problems.append(f"{label} ({step.tool}) returned status={status}")
        elif status in {"partial_success", "partial"}:
            problems.append(f"{label} ({step.tool}) returned partial success")
        elif not (step.result or "").strip():
            problems.append(f"{label} ({step.tool}) reported success with empty output")
    return problems


# --------------------------------------------------------------------------
# Question construction
# --------------------------------------------------------------------------

_SCREEN_PROMPT = "Given the task and the recorded tool trace, is the following true?"


def build_screen_questions() -> dict[str, Any]:
    """The whole-trace screen: one request, six questions."""
    return {
        "wrong_tool": BooleanQuestion(
            _SCREEN_PROMPT,
            {
                "true": "At least one step used a tool that does not fit what that step was trying to do.",
                "false": "Every step used a tool appropriate to its purpose.",
            },
        ),
        "args_mismatch": BooleanQuestion(
            _SCREEN_PROMPT,
            {
                "true": "At least one step's arguments disagree with its purpose — wrong path, wrong value, stale or placeholder parameter.",
                "false": "Arguments match what each step was trying to do.",
            },
        ),
        "unused_output": BooleanQuestion(
            _SCREEN_PROMPT,
            {
                "true": "At least one step produced output that no later step and not the final answer consumes.",
                "false": "Every step's output feeds a later step or the final answer.",
            },
        ),
        "cross_step_disagree": BooleanQuestion(
            _SCREEN_PROMPT,
            {
                "true": "Two or more steps contradict each other on a date, unit, identifier, or parameter.",
                "false": "Dates, units, identifiers and parameters are consistent across steps.",
            },
        ),
        "conclusion_unsupported": BooleanQuestion(
            _SCREEN_PROMPT,
            {
                "true": "The final answer asserts something the recorded results do not show.",
                "false": "The final answer follows from the recorded results.",
            },
        ),
        "severity": ScoreQuestion(
            "How serious is the worst problem in this trace?",
            SEVERITY_LEVELS,
        ),
    }


#: Which screen questions are *problems* (as opposed to the severity rubric).
PROBLEM_QUESTIONS = (
    "wrong_tool",
    "args_mismatch",
    "unused_output",
    "cross_step_disagree",
    "conclusion_unsupported",
)


def build_localise_questions(steps: list[TraceStep]) -> dict[str, Any]:
    """Per-step questions, used only after the screen fires."""
    questions: dict[str, Any] = {}
    for index, step in enumerate(steps[:LOCALISE_CAP]):
        label = step.step_id or f"step {index}"
        questions[f"{label}_wrong_tool"] = BooleanQuestion(
            f"Considering the step labelled '{label}' (tool: {step.tool}), is the following true?",
            {
                "true": "This step used a tool that does not fit what it was trying to do.",
                "false": "This step used an appropriate tool.",
            },
        )
        questions[f"{label}_args_mismatch"] = BooleanQuestion(
            f"Considering the step labelled '{label}' (tool: {step.tool}), is the following true?",
            {
                "true": "This step's arguments disagree with its purpose.",
                "false": "This step's arguments are appropriate.",
            },
        )
        questions[f"{label}_unused_output"] = BooleanQuestion(
            f"Considering the step labelled '{label}' (tool: {step.tool}), is the following true?",
            {
                "true": "This step's output is not consumed by any later step or by the final answer.",
                "false": "This step's output is used.",
            },
        )
    return questions


def build_state(task: str, answer: str, steps: list[TraceStep]) -> dict[str, Any]:
    """The state object sent to System One."""
    return {
        "task": task[:2000],
        "final_answer": answer[:2000],
        "steps": [s.to_state() for s in steps[:STEP_CAP]],
        "step_count": len(steps),
    }


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------


async def verify_trace(
    task: str,
    answer: str,
    steps: list[TraceStep],
    *,
    tier: str | RiskTier = RiskTier.READ,
    client: SystemOneClient | None = None,
    localize: bool = True,
) -> TraceVerdict | None:
    """Judge whether `steps` actually support `answer` for `task`.

    Returns None — never raises — when System One has no signal *and* the
    deterministic pass found nothing, which is the caller's cue to keep using
    today's receipt/acceptance layers unchanged.
    """
    problems = deterministic_problems(steps)
    cli = client or get_system_one_client()
    cfg = cli.config
    if not cfg.enabled or not cfg.enable_trace_verify:
        return TraceVerdict(trace_supported=None if not problems else False, problems=problems) if problems else None

    if not steps:
        return TraceVerdict(trace_supported=None, problems=problems) if problems else None

    state = build_state(task, answer, steps)
    threshold = cli.threshold_for(tier)

    try:
        result = await cli.evaluate(state, build_screen_questions(), min_confidence=threshold, site=SITE)
    except Exception as exc:  # never break the caller
        logger.debug("System One trace screen failed (%s); falling back.", exc)
        result = None
    if result is None:
        if not problems:
            return None
        return TraceVerdict(trace_supported=False, problems=problems)

    verdict = TraceVerdict(problems=list(problems), jev_used=True)

    for qid in PROBLEM_QUESTIONS:
        ans = result.get(qid)
        if ans is None or ans.type != "boolean":
            continue
        value = ans.boolean
        if value is None:
            continue
        if value >= FLAG_AT:
            verdict.problems.append(qid)
            if verdict.confidence is None:
                verdict.confidence = abs(value - 0.5) * 2

    severity_answer = result.get("severity")
    if severity_answer is not None and severity_answer.score is not None:
        # Normalise the 1-4 rubric onto 0..1 so callers have one scale.
        level = float(severity_answer.score)
        verdict.severity = round(min(1.0, max(0.0, (level - 1.0) / float(max(1, len(SEVERITY_LEVELS) - 1)))), 4)

    verdict.trace_supported = not verdict.problems

    if localize and verdict.problems:
        verdict.localized = await _localize(state, steps, cli, threshold)

    return verdict


async def _localize(
    state: dict[str, Any],
    steps: list[TraceStep],
    cli: SystemOneClient,
    threshold: float,
) -> list[str]:
    """Second stage: name the steps responsible. Best-effort only."""
    questions = build_localise_questions(steps)
    if not questions:
        return []
    try:
        result = await cli.evaluate(state, questions, min_confidence=threshold, site=f"{SITE}:localise")
    except Exception as exc:
        logger.debug("System One trace localisation failed (%s); skipping.", exc)
        return []
    if result is None:
        return []
    hits: list[str] = []
    for qid, question in questions.items():
        ans = result.get(qid)
        if ans is None or ans.type != "boolean":
            continue
        value = ans.boolean
        if value is not None and value >= FLAG_AT:
            hits.append(qid)
    return hits


def verify_trace_sync(
    task: str,
    answer: str,
    steps: list[TraceStep],
    *,
    tier: str | RiskTier = RiskTier.READ,
    client: SystemOneClient | None = None,
    localize: bool = True,
) -> TraceVerdict | None:
    """Sync wrapper for call sites already inside a running loop.

    Never blocks a live event loop: if one is running we return None (fall
    back) rather than deadlocking the caller.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(verify_trace(task, answer, steps, tier=tier, client=client, localize=localize))
    logger.debug("verify_trace_sync called inside a running loop; falling back.")
    return None


def render_trace_verdict(verdict: TraceVerdict | None) -> str:
    """Render for the delegation ledger / audit line."""
    if verdict is None:
        return ""
    if verdict.trace_supported is None:
        return "trace: UNKNOWN — no signal"
    if verdict.trace_supported:
        return "trace: supported — execution evidence only, does not validate correctness"
    detail = ", ".join(verdict.problems[:5])
    severity = f" (severity {verdict.severity:.2f})" if verdict.severity is not None else ""
    return f"trace: {len(verdict.problems)} issue(s){severity} — {detail}"


def steps_from_receipts(
    receipts: list[dict[str, Any]],
    *,
    arg_texts: dict[str, str] | None = None,
    result_texts: dict[str, str] | None = None,
) -> list[TraceStep]:
    """Adapt Alpha's receipt ledger into trace steps.

    Receipts carry hashes, not content (by design — they are a freshness
    stamp). Callers that still hold the raw args/results can pass them keyed by
    receipt id; what is missing simply renders blank and the semantic pass
    degrades to status-only evidence.
    """
    arg_texts = arg_texts or {}
    result_texts = result_texts or {}
    return [
        TraceStep(
            tool=str(r.get("tool_name") or ""),
            args=str(arg_texts.get(r.get("id", ""), "")),
            result=str(result_texts.get(r.get("id", ""), "")),
            status=str(r.get("status") or "success"),
            step_id=str(r.get("id") or ""),
        )
        for r in receipts
    ]


__all__ = [
    "FLAG_AT",
    "LOCALISE_CAP",
    "PROBLEM_QUESTIONS",
    "SEVERITY_LEVELS",
    "STEP_CAP",
    "VERDICT_REQUIREMENT",
    "VERDICT_SOURCE",
    "TraceStep",
    "TraceVerdict",
    "build_localise_questions",
    "build_screen_questions",
    "build_state",
    "deterministic_problems",
    "render_trace_verdict",
    "steps_from_receipts",
    "verify_trace",
    "verify_trace_sync",
]
