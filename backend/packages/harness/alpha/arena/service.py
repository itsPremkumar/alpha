"""The arena service: lifecycle, gates, feedback.

Everything the model surface (the ``arena`` tool) and
the HTTP surface (``/api/arena``) call lives here,
so both speak one contract:

* **Plan before spend.** ``estimate`` projects the
  exact call count and its estimated token cost.
  ``create`` deals the cards and persists a draft.
  Nothing is spent until ``start``.
* **The confirmation gate.** A run projected above
  ``arena.require_confirmation_over_calls`` needs an
  explicit ``confirm=True``. The gate is the direct
  answer to the upstream pattern's measured failure
  mode - a 100-agent default run costs ~39M tokens,
  and that must be a deliberate act, not a default.
* **Resumable.** ``resume`` drives the same executor
  loop from wherever a run stopped - a Gateway
  restart, a budget stop, a crash - losing at most
  one wave.
* **Learning loop.** Every match outcome is recorded
  into ``alpha.reasoning_bank`` (the winner's card,
  its evidence ref the run id), and every new run
  recalls strategies that worked before. The arena
  gets smarter the way the rest of Alpha does:
  measured, deterministic, no extra model call.

The service never asserts ownership: every read and
mutation is owner-checked by the store, because
request context cannot self-assert an owner.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from pathlib import Path
from typing import Any, Callable

from alpha.arena import bracket
from alpha.arena.cards import deal_cards, load_deck
from alpha.arena.config import ArenaConfig, arena_config
from alpha.arena.executor import ArenaExecutor, ArenaRunError
from alpha.arena.models import ArenaPlan
from alpha.arena.runner import ArenaAgentRunner, SubagentArenaRunner
from alpha.arena.store import ArenaStore, ArenaStoreError

logger = logging.getLogger(__name__)

#: Strategy-bank scope for arena outcomes.
REASONING_SCOPE = "arena"


class ArenaConfirmationRequired(RuntimeError):
    """The run's projected cost needs an explicit confirm."""

    def __init__(self, plan: ArenaPlan, estimated_tokens: int, ceiling: int) -> None:
        self.plan = plan
        self.estimated_tokens = estimated_tokens
        self.ceiling = ceiling
        super().__init__(
            f"arena run projected at {plan.total_calls} sub-agent calls "
            f"(~{estimated_tokens:,} estimated tokens) exceeds the confirmation "
            f"ceiling of {ceiling} calls; pass confirm=True to run it"
        )


class ArenaService:
    """Owns arena run lifecycle for one installation."""

    def __init__(
        self,
        store: ArenaStore | None = None,
        *,
        config: ArenaConfig | None = None,
        runner_factory: Callable[[Any], Any] | None = None,
        deck_path: Path | None = None,
    ) -> None:
        self._store = store or ArenaStore()
        self._config = config or arena_config()
        self._runner_factory = runner_factory
        self._deck_path = deck_path

    @property
    def config(self) -> ArenaConfig:
        return self._config

    @property
    def store(self) -> ArenaStore:
        return self._store

    # ------------------------------------------------------------------ plan

    def estimate(
        self,
        *,
        agents: int | None = None,
        wave: int | None = None,
        baseline: bool = False,
    ) -> dict[str, Any]:
        """Project a run's cost before anything exists.

        The projection is arithmetic, not a guess: the
        bracket shape fixes the match count, and every
        match costs ``CALLS_PER_MATCH`` sub-agent calls
        plus one spawn per competitor.
        """
        agents = self._resolve_agents(agents)
        wave = self._resolve_wave(wave)
        plan = bracket.plan(agents, wave, baseline=baseline)
        estimated_tokens = plan.total_calls * self._config.estimate_tokens_per_call
        return {
            "plan": plan.to_dict(),
            "estimated_tokens": estimated_tokens,
            "estimated_calls": plan.total_calls,
            "confirmation_required": (
                self._config.require_confirmation_over_calls > 0
                and plan.total_calls > self._config.require_confirmation_over_calls
            ),
            "confirmation_ceiling": self._config.require_confirmation_over_calls,
        }

    # ------------------------------------------------------------------ create

    def create(
        self,
        *,
        owner_id: str,
        thread_id: str | None,
        task: str,
        agents: int | None = None,
        wave: int | None = None,
        seed: str | int | None = None,
        baseline: str | None = None,
        max_subagent_calls: int | None = None,
        max_tokens: int | None = None,
        max_wall_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Deal the cards and persist a draft run.

        The deck is validated at load: a malformed deck
        is an error, never a partial arena. The seed
        defaults to a random one, so two runs of the same
        task deal different cards.
        """
        if not str(task).strip():
            raise ValueError("an arena needs a task")
        agents = self._resolve_agents(agents)
        wave = self._resolve_wave(wave)
        if seed is None:
            seed = random.randrange(2**32)
        state = bracket.new_run(
            owner_id=owner_id,
            thread_id=thread_id,
            task=task,
            seed=seed,
            agents_n=agents,
            wave=wave,
            baseline=baseline,
            max_subagent_calls=self._coalesce_budget(max_subagent_calls, "max_subagent_calls_per_run"),
            max_tokens=self._coalesce_budget(max_tokens, "max_tokens_per_run"),
            max_wall_seconds=self._coalesce_budget(max_wall_seconds, "max_wall_seconds_per_run"),
        )
        cards = deal_cards(agents, seed, self._load_deck())
        bracket.set_cards(state, {agent: card for agent, card in zip(bracket.agent_ids(agents), cards)})
        state["recalled"] = self._recall(task)
        self._store.ensure_dirs(state["run_id"])
        self._store.save(state)
        self._store.append_log(state["run_id"], f"created with {agents} competitors, seed {seed}")
        return state

    def _load_deck(self) -> dict[str, Any]:
        if self._deck_path is not None:
            return load_deck(self._deck_path)
        from alpha.arena.cards import DEFAULT_DECK_PATH

        return load_deck(DEFAULT_DECK_PATH)

    def _recall(self, task: str) -> list[str]:
        """Strategies that worked on similar tasks before.

        A bank failure is disclosed and never fails the
        run: recall is an enrichment, not a dependency.
        """
        if not self._config.reasoning_bank_seed:
            return []
        try:
            from alpha.reasoning_bank import get_reasoning_bank

            bank = get_reasoning_bank()
            records = bank.recall(task, scope=REASONING_SCOPE, limit=3)
            return [f"{record.strategy} (win rate {record.win_rate:.0%})" for record in records]
        except Exception:
            logger.warning("arena recall from reasoning_bank failed", exc_info=True)
            return []

    # ------------------------------------------------------------------ execute

    async def start(self, run_id: str, owner_id: str, *, confirm: bool = False) -> dict[str, Any]:
        """Run a draft to completion, behind the gate."""
        state = self._store.load(run_id, owner_id)
        plan = bracket.plan(state["agents_n"], state["wave"], baseline=bool(state.get("baseline")))
        estimated_tokens = plan.total_calls * self._config.estimate_tokens_per_call
        if (
            self._config.require_confirmation_over_calls > 0
            and plan.total_calls > self._config.require_confirmation_over_calls
            and not confirm
        ):
            raise ArenaConfirmationRequired(plan, estimated_tokens, self._config.require_confirmation_over_calls)
        return await self._drive(state)

    async def resume(self, run_id: str, owner_id: str) -> dict[str, Any]:
        """Drive a stopped or interrupted run onward.

        A terminal run cannot be resumed - completion,
        budget exhaustion and honest failure are
        terminal by design. Resume is for a run that
        stopped mid-bracket.
        """
        state = self._store.load(run_id, owner_id)
        if state["status"] in ("completed", "stopped", "failed", "budget_exhausted"):
            raise ArenaRunError(f"run is {state['status']} and cannot be resumed")
        return await self._drive(state)

    async def _drive(self, state: dict[str, Any]) -> dict[str, Any]:
        runner = self._build_runner()
        executor = ArenaExecutor(
            self._store,
            runner,
            wave=state["wave"],
            max_judge_attempts=self._config.max_judge_attempts,
            on_event=self._on_event,
        )
        try:
            state = await executor.execute(state)
        except ArenaRunError:
            state["status"] = "failed"
            state["stop_reason"] = "run_error"
            self._store.save(state)
            raise
        if state.get("champion") and self._config.reasoning_bank_seed:
            self._record_outcomes(state)
        return state

    def _build_runner(self) -> ArenaAgentRunner:
        if self._runner_factory is not None:
            # Call the factory to see what it returns. It might return
            # an ArenaAgentRunner directly (for testing) or a
            # SubagentExecutor factory (for production).
            factory_result = self._runner_factory(None)
            if hasattr(factory_result, "run_job"):
                return factory_result  # type: ignore[return-value]
            return SubagentArenaRunner(self._runner_factory)
        raise ArenaRunError(
            "no sub-agent runner is bound to this arena service; "
            "execution requires a request-bound SubagentExecutor factory"
        )

    def _on_event(self, name: str, payload: dict[str, Any]) -> None:
        """Publish on the in-process bus, off the hot path.

        The bus is process-local and non-blocking, so a
        slow consumer can never stall a tournament wave.
        """
        try:
            from alpha.events.bus import get_event_bus

            bus = get_event_bus()
            asyncio.get_running_loop().create_task(bus.publish(name, payload, source="arena"))
        except Exception:
            logger.warning("arena event publish failed for '%s'", name, exc_info=True)

    def _record_outcomes(self, state: dict[str, Any]) -> None:
        """Feed match outcomes into the reasoning bank.

        The winner's card is the strategy; the run id is
        the evidence ref (a success without evidence is
        demoted to unknown by the bank itself). A bank
        failure never fails a completed run.
        """
        try:
            from alpha.reasoning_bank import get_reasoning_bank

            bank = get_reasoning_bank()
            trigger = state["task"][:240]
            for round_record in state["rounds"]:
                for match in round_record["matches"]:
                    winner = match.get("winner")
                    if not winner:
                        continue
                    card = bracket.get_card(state, winner)
                    bank.record(
                        REASONING_SCOPE,
                        trigger,
                        card.line,
                        verdict="success",
                        evidence_ref=state["run_id"],
                        tags=(f"round-{round_record['round']}", "arena"),
                    )
        except Exception:
            logger.warning("arena reasoning_bank record failed", exc_info=True)

    # ------------------------------------------------------------------ reads

    def status(self, run_id: str, owner_id: str) -> dict[str, Any]:
        state = self._store.load(run_id, owner_id)
        summary = bracket.run_summary(state)
        summary["stop_reason"] = state.get("stop_reason")
        summary["failed"] = state.get("failed", {})
        summary["recalled"] = state.get("recalled", [])
        summary["log"] = [
            entry.get("line", "") for entry in self._store.read_log(run_id, tail=10)
        ]
        return summary

    def pairings(self, run_id: str, owner_id: str) -> dict[str, Any]:
        """The current round's pairings, with each match's
        progress - the bracket the model reads to follow
        a fight."""
        state = self._store.load(run_id, owner_id)
        round_record = state["rounds"][-1] if state["rounds"] else None
        if round_record is None:
            return {"round": 0, "status": "not started", "matches": [], "byes": []}
        matches = []
        for match in round_record["matches"]:
            entry = {
                "id": match["id"],
                "a": match["a"],
                "b": match["b"],
                "a_card": state["cards"].get(match["a"], {}).get("reasoning", {}).get("name"),
                "b_card": state["cards"].get(match["b"], {}).get("reasoning", {}).get("name"),
                "attacks_recorded": {
                    side: len(match["attacks"].get(side, [])) for side in (match["a"], match["b"])
                },
                "revised": {side: side in match["revised"] for side in (match["a"], match["b"])},
                "winner": match.get("winner"),
                "verdict": match.get("verdict"),
            }
            matches.append(entry)
        return {
            "round": round_record["round"],
            "status": round_record["status"],
            "matches": matches,
            "byes": round_record["byes"],
        }

    def card(self, run_id: str, owner_id: str, agent_id: str) -> dict[str, Any]:
        """The card dealt to one competitor."""
        state = self._store.load(run_id, owner_id)
        if agent_id not in state["cards"]:
            raise KeyError(f"no competitor '{agent_id}' in run '{run_id}'")
        return state["cards"][agent_id]

    def winner(self, run_id: str, owner_id: str) -> dict[str, Any]:
        """The champion, its card, and the final check."""
        state = self._store.load(run_id, owner_id)
        if not state.get("champion"):
            raise ArenaRunError(f"run '{run_id}' has no champion yet")
        return {
            "champion": state["champion"],
            "card": state["cards"].get(state["champion"]),
            "final": state.get("final"),
            "budget": state.get("budget"),
            "solution": state.get("solutions", {}).get(state["champion"]),
        }

    def list_runs(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        return self._store.list_runs(owner_id)

    # ------------------------------------------------------------------ control

    def stop(self, run_id: str, owner_id: str) -> dict[str, Any]:
        """Stop a running run. Terminal and deliberate."""
        state = self._store.load(run_id, owner_id)
        if state["status"] != "running":
            raise ArenaRunError(f"run is {state['status']}, not running")
        state["status"] = "stopped"
        state["stop_reason"] = "operator_stop"
        bracket.touch(state)
        self._store.save(state)
        self._store.append_log(run_id, "stopped by operator")
        return {"run_id": run_id, "status": state["status"]}

    def delete(self, run_id: str, owner_id: str) -> None:
        self._store.delete(run_id, owner_id)

    # ------------------------------------------------------------------ helpers

    def _resolve_agents(self, agents: int | None) -> int:
        if agents is None:
            return self._config.default_agents
        if agents < 1:
            raise ValueError("an arena needs at least 1 agent")
        if agents > self._config.max_agents:
            # Refused with the ceiling named, never clamped:
            # a bigger arena is the operator's call, not the
            # caller's guess.
            raise ValueError(f"arena is capped at {self._config.max_agents} agents")
        return agents

    def _resolve_wave(self, wave: int | None) -> int:
        if wave is None:
            return self._config.default_wave
        if wave < 1:
            raise ValueError("wave size must be at least 1")
        if wave > self._config.max_wave:
            raise ValueError(f"wave size is capped at {self._config.max_wave}")
        return wave

    def _coalesce_budget(self, value: Any, field: str) -> Any:
        """Caller-specified budget, else the config default.

        The caller may lower a ceiling freely; raising it
        is the confirmation gate's job, checked at
        ``start``.
        """
        if value is not None:
            return value
        return getattr(self._config, field, None)


__all__ = [
    "ArenaConfirmationRequired",
    "ArenaService",
    "REASONING_SCOPE",
]
