"""The tournament driver: waves, budgets, failure policy.

This is the loop that turns the bracket state machine
into real sub-agent work:

* **Waves.** Each pass asks the state machine for a
  wave-sized batch of *ready* jobs - attacks before
  defenses, defenses before the judge, always - and
  dispatches them concurrently. Jobs from different
  matches are independent, so a wave is real parallelism
  bounded by the caller's wave size.
* **Budgets.** Every job charges the run's measured
  budget (calls and tokens) from what the runner
  reported. When a ceiling is hit the run stops with
  ``budget_exhausted`` and the measured figures - never
  a rounded-down remainder, never a silent overrun.
* **Failure policy.** A failed spawn drops the
  competitor (recorded, not silently); a failed attack
  is *no attacks*; a failed defense leaves the original
  solution standing with every attack unanswered; a
  failed judge is re-run up to ``max_judge_attempts``
  and then fails the run honestly - the orchestrator
  never invents a winner.
* **Resumable.** The state is saved after every wave,
  so a crash or a Gateway restart loses at most one
  wave of work, and ``ArenaService.resume`` drives the
  same loop from where it stopped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

from alpha.arena import bracket
from alpha.arena.models import VerdictScores
from alpha.arena.rubric import RUBRIC_TEXT
from alpha.arena.prompts import card_values, render
from alpha.arena.runner import ArenaAgentRunner, ArenaJob, ArenaJobResult
from alpha.arena.store import ArenaStore

logger = logging.getLogger(__name__)

#: Default judge attempts before the run fails honestly.
DEFAULT_JUDGE_ATTEMPTS = 2


class ArenaRunError(RuntimeError):
    """A run cannot continue: the honest terminal state."""


class ArenaExecutor:
    """Drives one arena run to completion (or an honest stop)."""

    def __init__(
        self,
        store: ArenaStore,
        runner: ArenaAgentRunner,
        *,
        wave: int = 4,
        max_judge_attempts: int = DEFAULT_JUDGE_ATTEMPTS,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._wave = max(1, int(wave))
        self._max_judge_attempts = max(1, int(max_judge_attempts))
        self._on_event = on_event

    # ------------------------------------------------------------------ entry

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Drive ``state`` until done, exhausted, failed or stopped.

        Returns the final state. The caller persists it; the
        executor persists after every wave so a crash here
        loses at most one wave.
        """
        if state.get("status") == "draft":
            bracket.start_run(state)
            self._store.save(state)
        started = time.time()
        while not bracket.is_done(state):
            outcome = self._budget_outcome(state, started)
            if outcome is not None:
                self._stop(state, outcome)
                break
            actions = bracket.next_actions(state, limit=self._wave)
            if not actions:
                # No actions ready this wave - could be between rounds.
                # If the run is not done, continue to the next iteration
                # to let next_actions advance the bracket (e.g., start next round).
                if bracket.is_done(state):
                    break
                # Brief yield to avoid tight loop if truly stalled
                await asyncio.sleep(0)
                continue
            jobs = [self._build_job(state, action) for action in actions]
            results = await asyncio.gather(
                *(self._runner.run_job(job) for job in jobs)
            )
            for action, job, result in zip(actions, jobs, results):
                self._record(state, action, job, result)
            self._charge(state, results)
            self._store.save(state)
        self._emit("arena.run.completed", {"run_id": state["run_id"], "status": state["status"]})
        return state

    # ------------------------------------------------------------------ jobs

    def _build_job(self, state: dict[str, Any], action: dict[str, Any]) -> ArenaJob:
        kind = action["kind"]
        run_id = state["run_id"]
        if kind == "spawn":
            agent = action["agent"]
            card_values_ = card_values(bracket.get_card(state, agent))
            path = self._work_path(state, f"{agent}-solution.md")
            prompt = render(
                "competitor",
                {"task": state["task"], **card_values_, "solution_path": str(path)},
            )
            return ArenaJob(kind, run_id, agent, prompt, str(path))
        if kind == "attack":
            attacker, defender = action["attacker"], action["defender"]
            card_values_ = card_values(bracket.get_card(state, attacker))
            # Use the current solution for the defender (may be revised from previous round)
            solution_path = state["solutions"].get(defender)
            if not solution_path:
                raise ArenaRunError(f"no solution found for defender '{defender}'")
            # Extract filename from stored path
            solution_name = Path(solution_path).name
            path = self._work_path(state, solution_name)
            prompt = render(
                "attacker",
                {"task": state["task"], **card_values_, "solution_path": str(path)},
            )
            return ArenaJob(kind, run_id, attacker, prompt, "")
        if kind == "defend":
            defender, attacker = action["defender"], action["attacker"]
            card_values_ = card_values(bracket.get_card(state, defender))
            # Use the current solution for the defender
            solution_path = state["solutions"].get(defender)
            if not solution_path:
                raise ArenaRunError(f"no solution found for defender '{defender}'")
            solution_name = Path(solution_path).name
            solution = self._work_path(state, solution_name)
            revised = self._work_path(state, f"{defender}-revised-r{action['round']}.md")
            attacks_text = _format_attacks(self._match(state, action)["attacks"].get(attacker, []))
            prompt = render(
                "defender",
                {
                    "task": state["task"],
                    **card_values_,
                    "solution_path": str(solution),
                    "attacks_text": attacks_text,
                    "revised_path": str(revised),
                },
            )
            return ArenaJob(kind, run_id, defender, prompt, str(revised))
        if kind == "judge":
            match = self._match(state, action)
            a_solution = state["solutions"].get(match["a"])
            b_solution = state["solutions"].get(match["b"])
            if not a_solution or not b_solution:
                raise ArenaRunError(f"missing solution for judge: a={a_solution}, b={b_solution}")
            a_name = Path(a_solution).name
            b_name = Path(b_solution).name
            a_path = self._work_path(state, a_name)
            b_path = self._work_path(state, b_name)
            prompt = render(
                "judge",
                {
                    "task": state["task"],
                    "a_path": str(a_path),
                    "b_path": str(b_path),
                    "rubric_text": RUBRIC_TEXT,
                },
            )
            return ArenaJob(kind, run_id, "judge", prompt, "")
        if kind == "final":
            champion = action["agent"]
            champion_solution = state["solutions"].get(champion)
            if not champion_solution:
                raise ArenaRunError(f"no solution found for champion '{champion}'")
            path = self._work_path(state, champion_solution)
            prompt = render(
                "final",
                {
                    "task": state["task"],
                    "baseline_text": state.get("baseline") or "none supplied",
                    "champion_path": str(path),
                },
            )
            return ArenaJob(kind, run_id, champion, prompt, "")
        raise ArenaRunError(f"unknown arena action '{kind}'")

    def _work_path(self, state: dict[str, Any], name: str) -> Any:
        return self._store.work_path(state["run_id"], name)

    def _match(self, state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        _round_record, match = bracket.find_match(state, action["match"])
        return match

    # ------------------------------------------------------------------ recording

    def _record(
        self,
        state: dict[str, Any],
        action: dict[str, Any],
        job: ArenaJob,
        result: ArenaJobResult,
    ) -> None:
        kind = action["kind"]
        if not result.ok:
            self._record_failure(state, action, result)
            return
        if kind == "spawn":
            self._store.ensure_dirs(state["run_id"])
            path = self._work_path(state, f"{job.agent_id}-solution.md")
            if not self._exists(path):
                # The competitor promised a file and wrote none.
                bracket.mark_failed(state, job.agent_id, "competitor wrote no solution file")
                return
            bracket.record_solution(state, job.agent_id, str(path))
            return
        if kind == "attack":
            bracket.record_attack(state, action["match"], job.agent_id, result.text)
            return
        if kind == "defend":
            revised = self._work_path(state, f"{job.agent_id}-revised-r{action['round']}.md")
            if not self._exists(revised):
                # No revision landed: the original stands and
                # every attack against it survives.
                original = self._work_path(state, f"{job.agent_id}-solution.md")
                bracket.record_defense(state, action["match"], job.agent_id, "", str(original))
                return
            bracket.record_defense(state, action["match"], job.agent_id, result.text, str(revised))
            return
        if kind == "judge":
            verdict = self._parse_judge(result.text)
            if verdict is None:
                self._judge_failure(state, action, result)
                return
            bracket.record_verdict(
                state,
                action["match"],
                verdict["a"],
                verdict["b"],
                judge_pick=verdict.get("winner"),
                judge_reason=verdict.get("reason", ""),
            )
            match = self._match(state, action)
            self._emit(
                "arena.match.completed",
                {
                    "run_id": state["run_id"],
                    "match": action["match"],
                    "round": action["round"],
                    "winner": match.get("winner"),
                    "judge_overridden": match["verdict"]["judge_overridden"],
                },
            )
            return
        if kind == "final":
            passed, reason = _parse_final(result.text)
            bracket.record_final(state, result.text, passed)
            self._emit(
                "arena.final.completed",
                {"run_id": state["run_id"], "passed": passed, "reason": reason},
            )
            return
        raise ArenaRunError(f"unknown arena action '{kind}'")

    def _record_failure(
        self,
        state: dict[str, Any],
        action: dict[str, Any],
        result: ArenaJobResult,
    ) -> None:
        """The failure policy, per action kind."""
        kind = action["kind"]
        reason = result.error or "job failed"
        if kind == "spawn":
            bracket.mark_failed(state, action["agent"], reason)
            return
        if kind == "attack":
            # Silence is no attacks: the defender is told it
            # received none, which is the honest reading.
            bracket.record_attack(state, action["match"], action["attacker"], "")
            return
        if kind == "defend":
            # No revision landed: the original solution stands
            # and every attack against it survives.
            original = self._work_path(state, f"{action['defender']}-solution.md")
            bracket.record_defense(state, action["match"], action["defender"], "", str(original))
            return
        if kind == "judge":
            self._judge_failure(state, action, result)
            return
        if kind == "final":
            raise ArenaRunError(f"final verification failed: {reason}")
        raise ArenaRunError(f"unknown arena action '{kind}'")

    def _judge_failure(
        self,
        state: dict[str, Any],
        action: dict[str, Any],
        result: ArenaJobResult,
    ) -> None:
        """A judge that could not be parsed.

        One retry is allowed - models produce malformed
        JSON - but a judge that keeps failing never leaves
        the match to the orchestrator's guess: the run
        fails, naming the match and the reason.
        """
        _round_record, match = bracket.find_match(state, action["match"])
        attempts = int(match.get("judge_attempts", 0)) + 1
        match["judge_attempts"] = attempts
        if attempts >= self._max_judge_attempts:
            raise ArenaRunError(
                f"judge for match '{action['match']}' failed after {attempts} "
                f"attempts: {result.error or 'unparsable verdict'}"
            )
        logger.warning(
            "arena judge attempt %d/%d failed for run '%s' match '%s'",
            attempts,
            self._max_judge_attempts,
            state["run_id"],
            action["match"],
        )

    @staticmethod
    def _exists(path: Any) -> bool:
        try:
            return bool(path) and path.exists()
        except (OSError, ValueError):
            return False

    # ------------------------------------------------------------------ budget

    def _charge(self, state: dict[str, Any], results: list[ArenaJobResult]) -> None:
        budget = state["budget"]
        for result in results:
            budget["measured_calls"] += max(0, int(result.calls))
            budget["measured_tokens"] += max(0, int(result.tokens))

    def _budget_outcome(self, state: dict[str, Any], started: float) -> str | None:
        """The exhausted budget axis, if any."""
        budget = state["budget"]
        if budget.get("max_subagent_calls") is not None and budget["measured_calls"] >= budget["max_subagent_calls"]:
            return "subagent_calls"
        if budget.get("max_tokens") is not None and budget["measured_tokens"] >= budget["max_tokens"]:
            return "tokens"
        if budget.get("max_wall_seconds") is not None:
            elapsed = time.time() - (budget.get("started_at") or started)
            if elapsed >= budget["max_wall_seconds"]:
                return "wall_seconds"
        return None

    def _stop(self, state: dict[str, Any], outcome: str) -> None:
        state["status"] = "budget_exhausted" if outcome != "stalled" else "failed"
        state["stop_reason"] = outcome
        self._store.save(state)
        self._emit(
            "arena.budget.exhausted" if outcome != "stalled" else "arena.run.failed",
            {"run_id": state["run_id"], "reason": outcome, "budget": state["budget"]},
        )

    # ------------------------------------------------------------------ parsing

    @staticmethod
    def _parse_judge(text: str) -> dict[str, Any] | None:
        """Parse the judge's JSON verdict from its reply.

        Tolerates prose around the JSON (models wrap output
        in fences and commentary). Returns ``None`` when no
        JSON object with a ``scores`` block can be found -
        the caller decides whether that is a retry or a
        failure.
        """
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        scores = data.get("scores")
        if not isinstance(scores, dict) or "a" not in scores or "b" not in scores:
            return None

        def side(raw: Any) -> dict[str, Any] | None:
            if not isinstance(raw, dict):
                return None
            try:
                VerdictScores(
                    correctness=float(raw.get("correctness", 0)),
                    completeness=float(raw.get("completeness", 0)),
                    specificity=float(raw.get("specificity", 0)),
                    robustness=float(raw.get("robustness", 0)),
                    clarity=float(raw.get("clarity", 0)),
                    fatal=bool(raw.get("fatal", False)),
                )
            except (TypeError, ValueError):
                return None
            return {
                "correctness": float(raw.get("correctness", 0)),
                "completeness": float(raw.get("completeness", 0)),
                "specificity": float(raw.get("specificity", 0)),
                "robustness": float(raw.get("robustness", 0)),
                "clarity": float(raw.get("clarity", 0)),
                "fatal": bool(raw.get("fatal", False)),
            }

        a, b = side(scores.get("a")), side(scores.get("b"))
        if a is None or b is None:
            return None
        return {"a": a, "b": b, "winner": data.get("winner"), "reason": data.get("reason", "")}

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        if self._on_event is not None:
            try:
                self._on_event(name, payload)
            except Exception:  # an event must never break a run
                logger.exception("arena event handler failed for '%s'", name)


def _format_attacks(attacks: list[dict[str, Any]]) -> str:
    """Render a parsed attack list as the text the defender sees."""
    if not attacks:
        return "NO ATTACKS"
    lines: list[str] = []
    for attack in attacks:
        lines.append(f"ATTACK {attack.get('index', '?')} [{attack.get('severity', 'MINOR')}] {attack.get('title', '')}")
        if attack.get("where"):
            lines.append(f"Where: {attack['where']}")
        if attack.get("problem"):
            lines.append(f"Problem: {attack['problem']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _parse_final(text: str) -> tuple[bool, str]:
    """Parse the final verifier's ``VERDICT: PASS|FAIL`` reply."""
    verdict_line = ""
    reason = ""
    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("verdict:"):
            verdict_line = lowered
        elif lowered.startswith("reason:"):
            reason = stripped[len("reason:") :].strip()
    if "pass" in verdict_line and "fail" not in verdict_line:
        return True, reason
    if "fail" in verdict_line:
        return False, reason
    return False, reason or "final verifier gave no readable verdict"


__all__ = [
    "ArenaExecutor",
    "ArenaRunError",
    "DEFAULT_JUDGE_ATTEMPTS",
]
