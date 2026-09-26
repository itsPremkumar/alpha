"""The auxiliary judge: one strict JSON verdict per turn boundary.

Two properties are load-bearing and both are tested:

**Conservatism.** The judge is told, in the system prompt and enforced by
:func:`parse_verdict`, that ``done`` requires *concrete evidence* - a command
result, a file excerpt, a test output. A response that merely asserts success is
a ``continue``. Alpha's history is full of work that reported success without
evidence, so "looks done" is the specific failure this judge is built to refuse.

**Fail-open.** An unavailable judge, a network blip, a truncated response, a
legacy ``{"done": true}`` shape - all of them resolve to ``continue``. Never
``done`` (that would fake completion) and never a wedge (that would stop
progress). The turn budget in :mod:`state` is the real backstop, which is
exactly why fail-open is safe here.

The judge is also the authority the *engine* consults, never the agent: nothing
in the agent's own output is authoritative for whether the goal is complete. A
judge is injected as a plain callable returning raw text, so the parsing rules
are testable without a model and a real model can be dropped in unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

from alpha.mission.goalloop.contract import CompletionContract
from alpha.mission.goalloop.verdict import (
    JUDGE_UNAVAILABLE_REASON,
    JUDGE_UNREADABLE_REASON,
    GoalVerdict,
    ParsedVerdict,
    parse_verdict,
)

logger = logging.getLogger(__name__)

#: The response tail handed to the judge. The judge is deciding about the turn
#: that just happened, so the newest text is the relevant text.
DEFAULT_RESPONSE_TAIL_CHARS: Final[int] = 4 * 1024

#: The judge's system prompt. Every clause here is load-bearing; the
#: "evidence" clause is the one that stops premature completion.
JUDGE_SYSTEM_PROMPT: Final[str] = (
    "You judge whether a standing goal has been achieved. You are deliberately "
    "conservative and you are an independent checker, not a cheerleader.\n"
    "\n"
    "You are given: the standing goal, an optional completion contract, any "
    "extra criteria the user added mid-loop, and the tail of the agent's most "
    "recent response.\n"
    "\n"
    "Return STRICT one-line JSON and nothing else:\n"
    '{"verdict": "done"|"blocked"|"continue"|"wait", "reason": "<one sentence>"}\n'
    "\n"
    "RULES:\n"
    "1. Return 'done' ONLY when the goal is satisfied AND the response contains "
    "CONCRETE EVIDENCE of it - a command result, a file excerpt, a test output, "
    "a named artifact you can point at. A claim that it is finished, a summary "
    "of intent, or 'looks good' is NOT evidence. If you cannot quote the "
    "evidence, the verdict is 'continue'.\n"
    "2. If a completion contract is present, its verification field is the bar: "
    "judge 'done' only when that verification is demonstrably met.\n"
    "3. Return 'blocked' when the goal is genuinely unachievable - out of "
    "scope, impossible with what is available, or it needs a human decision. An "
    "impossible task is reported, never waved through as done.\n"
    "4. Return 'wait' only when progress is genuinely gated on something still "
    "running, and say what in the reason.\n"
    "5. Otherwise return 'continue'.\n"
    "6. Every extra criterion the user added mid-loop must also be satisfied "
    "before you may return 'done'.\n"
    "7. Your reason is ONE sentence. Do not return prose around the JSON."
)


@dataclass(frozen=True)
class JudgeRequest:
    """Everything the judge is allowed to see.

    Note what is absent: no system prompt, no tool list, no credentials. The
    judge is handed a read-only view of the goal and the turn's text, so it
    cannot act, only observe.
    """

    objective: str
    response_text: str
    contract: CompletionContract = field(default_factory=CompletionContract)
    subgoals: tuple[str, ...] = ()
    turn: int = 1
    max_turns: int = 20

    def render(self) -> str:
        blocks = [f"STANDING GOAL:\n{self.objective}"]
        if not self.contract.is_empty:
            blocks.append(f"COMPLETION CONTRACT:\n{self.contract.render()}")
        if self.subgoals:
            numbered = "\n".join(f"{index + 1}. {item}" for index, item in enumerate(self.subgoals))
            blocks.append(f"ADDITIONAL CRITERIA THE USER ADDED MID-LOOP (all must hold):\n{numbered}")
        blocks.append(f"TURN {self.turn}/{self.max_turns}\nAGENT'S MOST RECENT RESPONSE:\n{self.response_text}")
        return "\n\n".join(blocks)


@dataclass(frozen=True)
class JudgeOutcome:
    """The judge's answer after parsing and fail-open handling.

    ``verdict`` is always a real :class:`GoalVerdict`; there is no way to
    construct an outcome that says "unknown". ``degraded`` records that the
    verdict was not actually read from a judge and was defaulted to
    ``continue``, which is what an operator needs to see in a log.
    """

    verdict: GoalVerdict
    reason: str = ""
    degraded: bool = False
    raw: str = ""

    @property
    def is_done(self) -> bool:
        return self.verdict is GoalVerdict.DONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "degraded": self.degraded,
        }


#: A judge is any async callable returning the raw model text. Returning text
#: (not a verdict) is deliberate: parsing stays in one place, so a custom judge
#: cannot accidentally bypass the strict grammar.
JudgeFn = Callable[[JudgeRequest], Awaitable[str]]


def _tail(text: str, limit: int = DEFAULT_RESPONSE_TAIL_CHARS) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"[... {len(text) - limit} earlier characters omitted ...]\n{text[-limit:]}"


def build_request(
    objective: str,
    response_text: str,
    *,
    contract: CompletionContract | None = None,
    subgoals: list[str] | tuple[str, ...] = (),
    turn: int = 1,
    max_turns: int = 20,
) -> JudgeRequest:
    return JudgeRequest(
        objective=objective,
        response_text=_tail(response_text),
        contract=contract or CompletionContract(),
        subgoals=tuple(subgoals),
        turn=turn,
        max_turns=max_turns,
    )


class GoalJudge:
    """Calls an injected judge and turns whatever comes back into a verdict.

    The class owns the fail-open policy, not the model. :meth:`judge` never
    raises and never returns ``done`` unless a verdict actually parsed as
    ``done``.
    """

    def __init__(self, judge_fn: JudgeFn | None = None) -> None:
        self._judge_fn = judge_fn

    async def judge(self, request: JudgeRequest) -> JudgeOutcome:
        if self._judge_fn is None:
            return JudgeOutcome(
                verdict=GoalVerdict.CONTINUE,
                reason=JUDGE_UNAVAILABLE_REASON,
                degraded=True,
            )
        try:
            raw = await self._judge_fn(request)
        except Exception as exc:  # noqa: BLE001 - a broken judge must not stop the loop
            logger.warning("Goal judge failed (%s); failing open to 'continue'", type(exc).__name__)
            return JudgeOutcome(
                verdict=GoalVerdict.CONTINUE,
                reason=JUDGE_UNAVAILABLE_REASON,
                degraded=True,
            )
        return self.interpret(raw)

    @staticmethod
    def interpret(raw: str) -> JudgeOutcome:
        """Parse raw judge text. Unreadable text is ``continue``, never ``done``."""
        parsed: ParsedVerdict | None = parse_verdict(raw)
        if parsed is None:
            logger.warning("Goal judge returned unreadable output; failing open to 'continue'")
            return JudgeOutcome(
                verdict=GoalVerdict.CONTINUE,
                reason=JUDGE_UNREADABLE_REASON,
                degraded=True,
                raw=raw if isinstance(raw, str) else repr(raw),
            )
        return JudgeOutcome(verdict=parsed.verdict, reason=parsed.reason, raw=raw if isinstance(raw, str) else "")


def make_model_judge(model_name: str | None = None) -> JudgeFn:
    """Build a :class:`GoalJudge` backed by a real chat model.

    The returned callable resolves alpha's chat-model factory lazily, so
    importing this module never pulls in provider configuration. If no model can
    be resolved the callable raises, and :class:`GoalJudge` turns that into
    ``continue`` - the documented fail-open direction.
    """

    async def _call(request: JudgeRequest) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        from alpha.models.factory import create_chat_model

        model = create_chat_model(model_name)
        response = await model.ainvoke(
            [SystemMessage(content=JUDGE_SYSTEM_PROMPT), HumanMessage(content=request.render())],
        )
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
        return str(content)

    return _call


__all__ = [
    "DEFAULT_RESPONSE_TAIL_CHARS",
    "JUDGE_SYSTEM_PROMPT",
    "GoalJudge",
    "JudgeFn",
    "JudgeOutcome",
    "JudgeRequest",
    "build_request",
    "make_model_judge",
]
