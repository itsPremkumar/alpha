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
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alpha.arena import bracket
from alpha.arena.models import VerdictScores
from alpha.arena.prompts import card_values, render
from alpha.arena.repair import parse_repairs, unresolved_attacks
from alpha.arena.rubric import RUBRIC_TEXT
from alpha.arena.runner import ArenaAgentRunner, ArenaJob, ArenaJobResult
from alpha.arena.store import ArenaStore
from alpha.arena.task_rubrics import HardGate, apply_gates, pair_order

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
            await self._prepare_judges(state, actions)
            jobs = [self._build_job(state, action) for action in actions]
            results = await asyncio.gather(*(self._runner.run_job(job) for job in jobs))
            for action, job, result in zip(actions, jobs, results):
                self._record(state, action, job, result)
            self._charge(state, results)
            self._store.save(state)
        self._emit("arena.run.completed", {"run_id": state["run_id"], "status": state["status"]})
        return state

    # ------------------------------------------------------------------ jobs

    async def _prepare_judges(self, state: dict[str, Any], actions: list[dict[str, Any]]) -> None:
        """Decide how each pending judge sees its pair.

        Two bias controls, both recorded on the match so a reader can
        see exactly what the judge was shown:

        * **position** - ``pair_order`` deterministically decides
          whether the pair is presented in its natural order, so a
          judge cannot win by always picking the first solution;
        * **identity** - each solution is copied to a neutral
          ``judge-<match>-a|b.md`` path, so the judge reads
          ``agent-7``'s work without seeing ``agent-7``'s name.

        Both derive from the run seed, so a resume re-presents the
        identical pair. If the neutral copy cannot be written the
        judge still runs - on the original paths - with
        ``blind: false`` recorded rather than claimed.
        """
        for action in actions:
            if action["kind"] != "judge":
                continue
            match = self._match(state, action)
            if match.get("judge_presentation"):
                continue
            a_solution = state["solutions"].get(match["a"])
            b_solution = state["solutions"].get(match["b"])
            if not a_solution or not b_solution:
                raise ArenaRunError(f"missing solution for judge: a={a_solution}, b={b_solution}")
            swap = not pair_order(
                state.get("seed", ""),
                int(action.get("round", 0) or 0),
                _match_number(action["match"]),
            )
            left_agent, right_agent = (match["b"], match["a"]) if swap else (match["a"], match["b"])
            presentation: dict[str, Any] = {
                "swapped": swap,
                "blind": True,
                "a_agent": left_agent,
                "b_agent": right_agent,
            }
            try:
                presentation["a_path"] = await asyncio.to_thread(self._copy_for_judge, state, action["match"], "a", state["solutions"][left_agent])
                presentation["b_path"] = await asyncio.to_thread(self._copy_for_judge, state, action["match"], "b", state["solutions"][right_agent])
            except OSError:
                logger.warning(
                    "arena judge presentation for run '%s' match '%s' falls back to named paths",
                    state["run_id"],
                    action["match"],
                    exc_info=True,
                )
                presentation["blind"] = False
                presentation["a_path"] = str(self._work_path(state, Path(state["solutions"][left_agent]).name))
                presentation["b_path"] = str(self._work_path(state, Path(state["solutions"][right_agent]).name))
            match["judge_presentation"] = presentation

    def _copy_for_judge(self, state: dict[str, Any], match_id: str, label: str, solution_path: str) -> str:
        source = self._work_path(state, Path(solution_path).name)
        target = self._work_path(state, f"judge-{match_id}-{label}.md")
        shutil.copyfile(source, target)
        return str(target)

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
            presentation = match.get("judge_presentation") or {}
            a_path, b_path = presentation.get("a_path"), presentation.get("b_path")
            if not a_path or not b_path:
                raise ArenaRunError(f"missing judge presentation for match '{action['match']}'")
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
            self._record_repairs(state, action, result.text)
            return
        if kind == "judge":
            verdict = self._parse_judge(result.text)
            if verdict is None:
                self._judge_failure(state, action, result)
                return
            match = self._match(state, action)
            a_scores, b_scores, judge_pick, gates = self._to_physical(state, action, verdict)
            bracket.record_verdict(
                state,
                action["match"],
                a_scores,
                b_scores,
                judge_pick=judge_pick,
                judge_reason=verdict.get("reason", ""),
            )
            if gates:
                match["gates"] = gates
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

    def _record_repairs(self, state: dict[str, Any], action: dict[str, Any], text: str) -> None:
        """Capture the defender's per-attack dispositions.

        Dispositions are data, not decisions. They record which attacks
        the defender answered (FIXED/CONCEDED/REBUTTED) and which it
        never addressed (DEFERRED), so the judge and the operator can
        read the repair record instead of re-deriving it from prose.
        The orchestrator still never picks a winner from them.
        """
        entries = parse_repairs(text)
        if not entries:
            return
        match = self._match(state, action)
        side = action["defender"]
        match.setdefault("repairs", []).extend(entry.to_dict() for entry in entries)
        attacked = [int(attack.get("index", position + 1)) for attacker, attacks in match.get("attacks", {}).items() if attacker != side for position, attack in enumerate(attacks)]
        match.setdefault("repairs_unresolved", {})[side] = unresolved_attacks(attacked, entries)

    def _to_physical(self, state: dict[str, Any], action: dict[str, Any], verdict: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str | None, dict[str, Any]]:
        """Map a judge verdict from presented labels back to physical sides.

        The judge scores *label* A and label B; when the pair was
        presented swapped, label A carries ``match['b']``'s work. Hard
        gates are applied to the scores they belong to *before* the
        mapping, so a tripped gate always lands on the right solution.
        """
        match = self._match(state, action)
        presentation = match.get("judge_presentation") or {}
        swapped = bool(presentation.get("swapped"))
        a_label = "b" if swapped else "a"
        b_label = "a" if swapped else "b"
        raw_gates = verdict.get("gates") or {}
        a_scores = self._gated_scores(verdict[a_label], raw_gates.get(a_label, []))
        b_scores = self._gated_scores(verdict[b_label], raw_gates.get(b_label, []))
        pick = verdict.get("winner")
        if isinstance(pick, str) and pick in ("a", "b"):
            pick = {"a": "b", "b": "a"}[pick] if swapped else pick
        gates_report: dict[str, Any] = {}
        if raw_gates:
            gates_report = {
                match["a"]: [gate.to_dict() for gate in raw_gates.get(a_label, [])],
                match["b"]: [gate.to_dict() for gate in raw_gates.get(b_label, [])],
            }
        return a_scores, b_scores, pick, gates_report

    @staticmethod
    def _gated_scores(raw: dict[str, Any], gates: list[HardGate]) -> dict[str, Any]:
        """The judge's scores with every tripped hard gate applied."""
        scores = VerdictScores(
            correctness=float(raw.get("correctness", 0)),
            completeness=float(raw.get("completeness", 0)),
            specificity=float(raw.get("specificity", 0)),
            robustness=float(raw.get("robustness", 0)),
            clarity=float(raw.get("clarity", 0)),
            fatal=bool(raw.get("fatal", False)),
        )
        adjusted, _ = apply_gates(scores, gates)
        return {
            "correctness": adjusted.correctness,
            "completeness": adjusted.completeness,
            "specificity": adjusted.specificity,
            "robustness": adjusted.robustness,
            "clarity": adjusted.clarity,
            "fatal": adjusted.fatal,
        }

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
            raise ArenaRunError(f"judge for match '{action['match']}' failed after {attempts} attempts: {result.error or 'unparsable verdict'}")
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
        verdict: dict[str, Any] = {"a": a, "b": b, "winner": data.get("winner"), "reason": data.get("reason", "")}
        gates = ArenaExecutor._parse_gates(data.get("gates"))
        if gates:
            verdict["gates"] = gates
        return verdict

    @staticmethod
    def _parse_gates(raw: Any) -> dict[str, list[HardGate]]:
        """Optional per-side hard gates from the judge's JSON.

        A judge that reports no gates yields an empty mapping - gates
        are never invented, and a malformed entry is dropped rather
        than guessed at, so an unreadable gate cannot fail a solution.
        """
        if not isinstance(raw, dict):
            return {}
        parsed: dict[str, list[HardGate]] = {}
        for label in ("a", "b"):
            entries = raw.get(label)
            if not isinstance(entries, list):
                continue
            gates: list[HardGate] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                gate_id = str(entry.get("id") or "").strip()
                if not gate_id:
                    continue
                gates.append(
                    HardGate(
                        id=gate_id,
                        description=str(entry.get("description") or gate_id),
                        tripped=bool(entry.get("tripped", False)),
                        detail=str(entry.get("detail") or ""),
                    )
                )
            if gates:
                parsed[label] = gates
        return parsed

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        if self._on_event is not None:
            try:
                self._on_event(name, payload)
            except Exception:  # an event must never break a run
                logger.exception("arena event handler failed for '%s'", name)


def _match_number(match_id: Any) -> int:
    """The numeric part of a match id, for deterministic pair ordering.

    A match id with no digits maps to 0 rather than raising: the order
    decision must never be the reason a run cannot proceed.
    """
    digits = "".join(character for character in str(match_id) if character.isdigit())
    return int(digits) if digits else 0


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
