"""The bracket state machine: pure functions over one dict.

The whole tournament lives in a single dict that round-trips to
JSON with no translation layer, so a run is: one file, one
atomic write, resumable by any process. Every function here is
pure - it takes the state, returns a new state or a description
of the next work - which is what makes the engine testable
with zero model calls.

Call budget arithmetic (single elimination):

* ``n`` spawn calls, one per competitor;
* ``n - 1`` matches (every match eliminates exactly one
  competitor), each costing ``CALLS_PER_MATCH = 5`` calls:
  two attacks, two defend-and-revise, one judge;
* one final check when a baseline is supplied.

So ``n = 8`` costs ``8 + 5 * 7 = 43`` calls. The planner
computes this *before* anything is spent, which is what the
budget gate uses.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Any

from alpha.arena.models import (
    ArenaPhase,
    ArenaPlan,
    ArenaPlanRow,
    ArenaStatus,
    AttackRecord,
    DefenseRecord,
    StrategyCard,
    parse_attacks,
    parse_defenses,
)
from alpha.arena.rubric import decide

#: Sub-agent calls one match costs: two attacks, two
#: defend-and-revise, one judge.
CALLS_PER_MATCH = 5

#: Competitors are ``agent-1`` .. ``agent-n``. Stable ids mean a
#: run resumed mid-bracket finds the same agents.


def agent_ids(n: int) -> list[str]:
    """The ``n`` competitor ids, stable and ordered."""
    return [f"agent-{i}" for i in range(1, n + 1)]


def bracket_sizes(n: int) -> list[tuple[int, int, bool]]:
    """The shape of a single-elimination bracket over ``n``.

    Returns one ``(alive, matches, bye)`` row per round. An odd
    alive count gives one competitor a bye into the next round;
    byes are handed to the lowest ids first so the choice is
    deterministic for a given dealt order.
    """
    rows: list[tuple[int, int, bool]] = []
    alive = n
    while alive > 1:
        matches = alive // 2
        bye = alive % 2 == 1
        rows.append((alive, matches, bye))
        alive = matches + (1 if bye else 0)
    return rows


def plan(n: int, wave: int, *, baseline: bool = False) -> ArenaPlan:
    """Project the cost of a run before anything is spent.

    The projection is the honest answer to "what will this
    cost": rounds, total calls, waves at the requested wave
    size, and a per-round breakdown. The budget gate reads
    ``total_calls``; the model reads ``rows``.
    """
    if n < 1:
        raise ValueError("an arena needs at least 1 agent")
    wave = max(1, int(wave))
    rows: list[ArenaPlanRow] = []
    calls = n
    waves = 0
    for round_no, (alive, matches, bye) in enumerate(bracket_sizes(n), start=1):
        round_waves = max(1, math.ceil(matches / wave))
        waves += round_waves
        rows.append(
            ArenaPlanRow(
                round=round_no,
                alive=alive,
                matches=matches,
                bye=bye,
                calls=matches * CALLS_PER_MATCH,
                waves=round_waves,
            )
        )
        calls += matches * CALLS_PER_MATCH
    return ArenaPlan(
        agents=n,
        rounds=len(rows),
        calls=calls,
        waves=waves,
        wave_size=wave,
        final_check=baseline,
        rows=rows,
    )


def new_run(
    *,
    run_id: str | None = None,
    owner_id: str,
    thread_id: str | None = None,
    task: str,
    seed: str | int,
    agents_n: int,
    wave: int = 4,
    baseline: str | None = None,
    max_subagent_calls: int | None = None,
    max_tokens: int | None = None,
    max_wall_seconds: float | None = None,
) -> dict[str, Any]:
    """Create a fresh run in the ``draft`` phase.

    Nothing has been spent: the run holds the task, the deal
    parameters and the budget ceilings. Cards are dealt and
    solutions appear when the executor drives the spawn phase.
    """
    if agents_n < 1:
        raise ValueError("an arena needs at least 1 agent")
    now = time.time()
    return {
        "run_id": run_id or f"arena-{uuid.uuid4().hex[:12]}",
        "owner_id": owner_id,
        "thread_id": thread_id,
        "task": task,
        "baseline": baseline,
        "seed": str(seed),
        "agents_n": agents_n,
        "wave": max(1, int(wave)),
        "status": ArenaStatus.DRAFT.value,
        "phase": ArenaPhase.DRAFT.value,
        "cards": {},
        "solutions": {},
        "failed": {},
        "rounds": [],
        "champion": None,
        "final": None,
        "budget": {
            "max_subagent_calls": max_subagent_calls,
            "max_tokens": max_tokens,
            "max_wall_seconds": max_wall_seconds,
            "measured_calls": 0,
            "measured_tokens": 0,
            "started_at": None,
        },
        "created_at": now,
        "updated_at": now,
    }


# ------------------------------------------------------------------ cards


def set_cards(state: dict[str, Any], cards: dict[str, StrategyCard]) -> None:
    """Record the dealt cards. One card per competitor, dealt
    before the first spawn."""
    if len(cards) != state["agents_n"]:
        raise ValueError(f"dealt {len(cards)} cards for {state['agents_n']} agents")
    state["cards"] = {agent: card.to_dict() for agent, card in cards.items()}
    touch(state)


def get_card(state: dict[str, Any], agent_id: str) -> StrategyCard:
    """The card dealt to one competitor."""
    return StrategyCard.from_dict(state["cards"][agent_id])


# ------------------------------------------------------------------ spawn


def mark_failed(state: dict[str, Any], agent_id: str, reason: str) -> None:
    """Record a competitor that could not produce a solution.

    A failed spawn is not a loss in the bracket - the
    competitor never entered it - but it is recorded, so
    the run reports honestly which agents dropped out and
    why. Failed agents are excluded from every round.
    """
    state["failed"][agent_id] = str(reason)[:500]
    state["solutions"].pop(agent_id, None)
    touch(state)


def alive_agents(state: dict[str, Any]) -> list[str]:
    """The competitors still able to fight, in dealt order.

    A competitor is out when it failed to produce a
    solution (spawn failure) - it is not silently
    dropped from the report.
    """
    return [agent for agent in agent_ids(state["agents_n"]) if agent not in state["failed"] and agent in state["solutions"]]


def start_run(state: dict[str, Any]) -> list[str]:
    """Move a draft run into the spawn phase. Returns the ids
    to spawn, in dealt order."""
    _require_status(state, {ArenaStatus.DRAFT, ArenaStatus.PAUSED})
    state["status"] = ArenaStatus.RUNNING.value
    state["phase"] = ArenaPhase.SPAWN.value
    state["budget"]["started_at"] = state["budget"]["started_at"] or time.time()
    touch(state)
    return agent_ids(state["agents_n"])


def record_solution(state: dict[str, Any], agent_id: str, path: str) -> None:
    """Record the file a competitor produced in the spawn phase."""
    state["solutions"][agent_id] = path
    touch(state)


def spawn_complete(state: dict[str, Any]) -> bool:
    """True when every competitor has produced a solution
    or failed to."""
    return len(state["solutions"]) + len(state["failed"]) >= state["agents_n"]


# ------------------------------------------------------------------ rounds


def _current_round(state: dict[str, Any]) -> dict[str, Any] | None:
    return state["rounds"][-1] if state["rounds"] else None


def start_round(state: dict[str, Any]) -> dict[str, Any]:
    """Open the next round: pair the alive competitors.

    Returns the round record. Pairing is deterministic for the
    run: the alive list keeps dealt order (winners take their
    predecessor's slot, byes sit at the end), and pairs are
    adjacent. A deterministic pairing is what makes a resumed
    run reproduce the same bracket.
    """
    round_no = len(state["rounds"]) + 1
    if round_no == 1:
        alive = alive_agents(state)
    else:
        alive = _alive_after_round(state)
    if not alive:
        raise ValueError("no competitors left to pair")
    matches: list[dict[str, Any]] = []
    bye_ids: list[str] = []
    i = 0
    while i < len(alive):
        if i + 1 >= len(alive):
            bye_ids.append(alive[i])
            i += 1
            continue
        matches.append(
            {
                "id": f"m{round_no}-{len(matches) + 1}",
                "a": alive[i],
                "b": alive[i + 1],
                "attacks": {},
                "defenses": {},
                "revised": {},
                "verdict": None,
                "winner": None,
                "loser": None,
            }
        )
        i += 2
    round_record: dict[str, Any] = {
        "round": round_no,
        "matches": matches,
        "byes": bye_ids,
        "status": "pending",
    }
    state["rounds"].append(round_record)
    state["phase"] = ArenaPhase.ATTACK.value
    touch(state)
    return round_record


def _alive_after_round(state: dict[str, Any]) -> list[str]:
    """The competitors who won the last completed round, in bracket order.

    Winners keep the slot of the pair they won; a bye keeps its
    own slot. This is what makes pairing deterministic across a
    resume: the alive list is a pure function of the recorded
    results.
    """
    # Find the last completed round
    last_complete = None
    for round_record in reversed(state["rounds"]):
        if round_record["status"] == "complete":
            last_complete = round_record
            break
    if last_complete is None:
        return []
    alive: list[str] = []
    for match in last_complete["matches"]:
        if match["winner"]:
            alive.append(match["winner"])
    alive.extend(last_complete["byes"])
    return alive


def find_match(state: dict[str, Any], match_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Locate a match by id: ``(round_record, match)``."""
    for round_record in state["rounds"]:
        for match in round_record["matches"]:
            if match["id"] == match_id:
                return round_record, match
    raise KeyError(f"no match '{match_id}'")


# ------------------------------------------------------------------ jobs


def next_actions(state: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    """The next batch of sub-agent work the executor can dispatch.

    This is the whole driver contract: the executor repeatedly
    asks for a wave-sized batch, dispatches it, records the
    results, and asks again. Dependencies are honoured inside a
    match - attacks before defenses, defenses before the judge -
    so a batch never contains a job whose inputs are not yet on
    disk. Jobs from different matches are independent and may
    run in any order or concurrently.
    """
    actions: list[dict[str, Any]] = []
    if state["status"] != ArenaStatus.RUNNING.value:
        return actions

    if state["phase"] == ArenaPhase.SPAWN.value:
        for agent in agent_ids(state["agents_n"]):
            if agent in state["failed"] or agent in state["solutions"]:
                continue
            actions.append({"kind": "spawn", "agent": agent})
            if len(actions) >= limit:
                return actions
        if not actions and spawn_complete(state):
            state["phase"] = ArenaPhase.ATTACK.value
            touch(state)
            actions = next_actions(state, limit)
        return actions

    if state["phase"] == ArenaPhase.FINAL.value:
        if state.get("final") is None and state["baseline"]:
            actions.append({"kind": "final", "agent": state["champion"]})
        return actions

    if state["phase"] in (ArenaPhase.ATTACK, ArenaPhase.DEFEND, ArenaPhase.JUDGE):
        round_record = _current_round(state)
        if round_record is None:
            # Spawn finished but no round opened: open it.
            if state["phase"] == ArenaPhase.ATTACK.value:
                start_round(state)
                round_record = _current_round(state)
            else:
                return actions
        # If the current round is complete, start the next round.
        if round_record["status"] == "complete":
            start_round(state)
            round_record = _current_round(state)
        for match in round_record["matches"]:
            if len(actions) >= limit:
                break
            a, b = match["a"], match["b"]
            if a not in state["solutions"] or b not in state["solutions"]:
                continue
            # 1. attacks: each side attacks the other's solution.
            if a not in match["attacks"]:
                actions.append(
                    {
                        "kind": "attack",
                        "round": round_record["round"],
                        "match": match["id"],
                        "attacker": a,
                        "defender": b,
                    }
                )
            if b not in match["attacks"]:
                actions.append(
                    {
                        "kind": "attack",
                        "round": round_record["round"],
                        "match": match["id"],
                        "attacker": b,
                        "defender": a,
                    }
                )
            # 2. defenses: each side answers the other's attacks
            #    and revises its solution.
            if len(match["attacks"]) >= 2 and a not in match["revised"]:
                actions.append(
                    {
                        "kind": "defend",
                        "round": round_record["round"],
                        "match": match["id"],
                        "defender": a,
                        "attacker": b,
                    }
                )
            if len(match["attacks"]) >= 2 and b not in match["revised"]:
                actions.append(
                    {
                        "kind": "defend",
                        "round": round_record["round"],
                        "match": match["id"],
                        "defender": b,
                        "attacker": a,
                    }
                )
            # 3. judge: score both revised solutions.
            if len(match["revised"]) >= 2 and match["verdict"] is None:
                actions.append(
                    {
                        "kind": "judge",
                        "round": round_record["round"],
                        "match": match["id"],
                    }
                )
        if actions:
            return actions
        # Nothing pending: the round is either complete or a
        # wave boundary was hit; finish it and open the next.
        if _round_complete(round_record):
            _complete_round(state, round_record)
            # After completing the round, the next call to next_actions
            # will start the next round (if any). Avoid infinite recursion
            # by not calling next_actions recursively here; instead return
            # empty and let the executor loop call next_actions again.
            return []
        return actions

    return actions


def _round_complete(round_record: dict[str, Any]) -> bool:
    return all(match["verdict"] is not None for match in round_record["matches"])


def _complete_round(state: dict[str, Any], round_record: dict[str, Any]) -> None:
    """Fold a finished round into the bracket: winners advance,
    the champion is decided when one competitor remains."""
    round_record["status"] = "complete"
    alive = _alive_after_round(state)
    if len(alive) == 1:
        state["champion"] = alive[0]
        state["phase"] = ArenaPhase.FINAL.value
        state["status"] = ArenaStatus.COMPLETED.value
        touch(state)
        return
    # More than one survives: open the next round lazily, on the
    # next call to next_actions.
    if state["phase"] == ArenaPhase.JUDGE.value:
        state["phase"] = ArenaPhase.ATTACK.value
    touch(state)


# ------------------------------------------------------------------ results


def record_attack(state: dict[str, Any], match_id: str, attacker: str, text: str) -> None:
    """Record an attacker's parsed attacks against a solution."""
    _round_record, match = find_match(state, match_id)
    if attacker not in (match["a"], match["b"]):
        raise ValueError(f"agent '{attacker}' is not in match '{match_id}'")
    match["attacks"][attacker] = [attack.to_dict() for attack in parse_attacks(text)]
    touch(state)


def record_defense(
    state: dict[str, Any],
    match_id: str,
    defender: str,
    text: str,
    revised_path: str,
) -> None:
    """Record a defender's answers and its revised solution file.

    The revised solution becomes the agent's current solution for
    the next round.
    """
    _round_record, match = find_match(state, match_id)
    if defender not in (match["a"], match["b"]):
        raise ValueError(f"agent '{defender}' is not in match '{match_id}'")
    match["defenses"][defender] = [defense.to_dict() for defense in parse_defenses(text)]
    match["revised"][defender] = revised_path
    state["solutions"][defender] = revised_path
    touch(state)


def record_verdict(
    state: dict[str, Any],
    match_id: str,
    a_scores: dict[str, Any],
    b_scores: dict[str, Any],
    judge_pick: str | None = None,
    judge_reason: str = "",
) -> dict[str, Any]:
    """Record the judge's scores and compute the winner.

    The judge's own pick is advisory: ``decide()`` recomputes the
    winner from the rubric arithmetic and records
    ``judge_overridden`` when the two disagree. The orchestrator
    never picks a winner of its own - it can only compute.
    """
    from alpha.arena.models import VerdictScores

    _round_record, match = find_match(state, match_id)
    if len(match["revised"]) < 2:
        raise ValueError(f"match '{match_id}' has no revised solutions to judge")
    verdict = decide(
        VerdictScores.from_dict(a_scores),
        VerdictScores.from_dict(b_scores),
        [AttackRecord.from_dict(attack) for attack in match["attacks"].get(match["a"], [])],
        [DefenseRecord.from_dict(defense) for defense in match["defenses"].get(match["a"], [])],
        [AttackRecord.from_dict(attack) for attack in match["attacks"].get(match["b"], [])],
        [DefenseRecord.from_dict(defense) for defense in match["defenses"].get(match["b"], [])],
        judge_pick,
    )
    verdict["judge_pick"] = judge_pick
    verdict["judge_reason"] = judge_reason
    match["verdict"] = verdict
    winner = verdict["winner"]
    if winner == "a":
        match["winner"], match["loser"] = match["a"], match["b"]
    elif winner == "b":
        match["winner"], match["loser"] = match["b"], match["a"]
    touch(state)
    return verdict


# ------------------------------------------------------------------ final


def record_final(state: dict[str, Any], text: str, passed: bool) -> None:
    """Record the final check of the champion against the baseline."""
    state["final"] = {"text": text, "passed": passed}
    touch(state)


# ------------------------------------------------------------------ status


def is_done(state: dict[str, Any]) -> bool:
    """True when the tournament has a champion (and, when a
    baseline was supplied, the final check has run)."""
    if state["champion"] is None:
        return False
    if state["baseline"] and state.get("final") is None:
        return False
    return True


def run_summary(state: dict[str, Any]) -> dict[str, Any]:
    """A bounded, model-readable summary of a run."""
    rounds = [
        {
            "round": round_record["round"],
            "matches": len(round_record["matches"]),
            "byes": len(round_record["byes"]),
            "status": round_record["status"],
        }
        for round_record in state["rounds"]
    ]
    return {
        "run_id": state["run_id"],
        "status": state["status"],
        "phase": state["phase"],
        "agents_n": state["agents_n"],
        "wave": state["wave"],
        "cards_dealt": len(state["cards"]),
        "solutions": len(state["solutions"]),
        "rounds": rounds,
        "rounds_complete": sum(1 for record in state["rounds"] if record["status"] == "complete"),
        "champion": state["champion"],
        "champion_card": state["cards"].get(state["champion"]) if state["champion"] else None,
        "budget": state["budget"],
    }


def touch(state: dict[str, Any]) -> None:
    """Stamp the mutation time. Called by every mutator."""
    state["updated_at"] = time.time()


def _require_status(state: dict[str, Any], allowed: set[ArenaStatus]) -> None:
    status = ArenaStatus(state["status"])
    if status not in allowed:
        raise ValueError(f"run is {status.value}, expected one of {sorted(s.value for s in allowed)}")


__all__ = [
    "CALLS_PER_MATCH",
    "agent_ids",
    "bracket_sizes",
    "find_match",
    "get_card",
    "is_done",
    "new_run",
    "next_actions",
    "plan",
    "record_attack",
    "record_defense",
    "record_final",
    "record_solution",
    "record_verdict",
    "run_summary",
    "set_cards",
    "spawn_complete",
    "start_round",
    "start_run",
]
