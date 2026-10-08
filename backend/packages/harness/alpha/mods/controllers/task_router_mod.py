"""Autonomous Task Router Mod (TaskRouterMod).

Matches incoming task requirements with registered bot capabilities, optimal model tiers,
and recommended toolsets. Enforces capability boundaries, avoids overloaded bots,
and routes tasks to specialists across the fleet.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from alpha.bots.registry import BotRegistry, get_bot_registry
from alpha.bots.work_discovery import match_bot_for_task
from alpha.capabilities.eligibility import eligible_candidates, infer_capability_tags
from alpha.models.task_router import TASK_TO_CATEGORY, aroute_task
from alpha.mods.context import CapabilityContext
from alpha.mods.types import (
    AlphaEvent,
    EventResult,
    ModPriority,
    NextHandler,
)

logger = logging.getLogger(__name__)


class TaskRouterMod:
    """Autonomous Task Router Mod.

    Intercepts task admission, routing, and bot assignment events:
    1. Analyzes task descriptions to extract required capability tags.
    2. Matches requirements against registered bot profiles in BotRegistry.
    3. Selects optimal model tier (e.g. ultrabrain for reasoning, deep for coding).
    4. Enforces bot status gates (refuses tasks for archived/suspended bots).
    5. Answers routing queries directly or rewrites task admission events.
    """

    name: str = "task_router"
    version: str = "1.0.0"
    priority: int = int(ModPriority.AUTONOMY)
    # Routing currently uses the trusted BotRegistry and deterministic model router
    # directly; it does not need ambient model, tool-execution, or evidence access.
    required_capabilities: set[str] = set()
    subscribed_events: set[str] = {
        "task.routed",
        "bot.task_assigned",
        "agent.spawn_requested",
        "run.admit",
        "task.admit",
    }

    def __init__(self, *, registry: BotRegistry | None = None, max_history: int = 100):
        self._registry = registry
        self._max_history = max_history
        self._routing_history: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    @property
    def registry(self) -> BotRegistry:
        return self._registry or get_bot_registry()

    def get_routing_history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._routing_history[-limit:])

    # -------------------------------------------------------------------------
    # Core Analysis & Routing Logic
    # -------------------------------------------------------------------------

    async def analyze_task(
        self,
        task_text: str,
        *,
        task_type: str | None = None,
        candidate_pool: list[str] | None = None,
    ) -> dict[str, Any]:
        """Analyze a task description and determine optimal bot, model category, and toolsets."""
        reg = self.registry

        # 1. Infer capability tags
        required_tags = sorted(infer_capability_tags(task_text))

        # 2. Map task type to model category
        inferred_type = (task_type or "").lower().strip()
        if not inferred_type:
            # Simple keyword heuristic if type is omitted
            lower_text = task_text.lower()
            if any(k in lower_text for k in ("reason", "math", "proof", "architect", "strategy")):
                inferred_type = "reasoning"
            elif any(k in lower_text for k in ("code", "python", "bug", "refactor", "function", "test")):
                inferred_type = "coding"
            elif any(k in lower_text for k in ("ui", "css", "frontend", "react", "html")):
                inferred_type = "frontend"
            elif any(k in lower_text for k in ("write", "doc", "article", "summary")):
                inferred_type = "writing"
            elif any(k in lower_text for k in ("research", "search", "investigate", "explore")):
                inferred_type = "research"
            else:
                inferred_type = "quick"

        category = TASK_TO_CATEGORY.get(inferred_type, "deep")

        # 3. Model routing decision
        try:
            model_route = (await aroute_task(inferred_type, task_text)).to_dict()
        except Exception:
            model_route = {"category": category, "primary": "", "chain": []}

        # 4. Bot matching
        ranked_bots = match_bot_for_task(task_text, registry=reg, limit=10)
        all_bots = reg.list_bots()

        # Filter by hard eligibility if tags exist
        if required_tags:
            eligibility = eligible_candidates(all_bots, required_tags)
            eligible_names = set(eligibility.eligible)
            # Eligibility is a hard constraint. An empty eligible set must
            # remain empty; falling back to the unfiltered ranking can assign
            # work to a bot that explicitly lacks the required skills.
            ranked_bots = [b for b in ranked_bots if str(b["bot_name"]).lower() in eligible_names]

        # Filter by candidate pool if specified
        if candidate_pool:
            pool_set = {c.lower() for c in candidate_pool}
            ranked_bots = [b for b in ranked_bots if b["bot_name"].lower() in pool_set]

        selected_bot_name = ranked_bots[0]["bot_name"] if ranked_bots else None
        selected_bot_profile = reg.get_bot(selected_bot_name) if selected_bot_name else None

        decision = {
            "task_type": inferred_type,
            "category": category,
            "required_capabilities": required_tags,
            "selected_bot": selected_bot_name,
            "selected_bot_role": selected_bot_profile.role if selected_bot_profile else None,
            "recommended_toolsets": selected_bot_profile.toolsets if selected_bot_profile else [],
            "model_route": model_route,
            "ranked_candidates": ranked_bots[:5],
            "timestamp": time.time(),
        }

        with self._lock:
            self._routing_history.append(decision)
            if len(self._routing_history) > self._max_history:
                self._routing_history.pop(0)

        return decision

    # -------------------------------------------------------------------------
    # Mod Handler Pipeline
    # -------------------------------------------------------------------------

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        ev_name = event.name
        payload = event.payload

        # 1. Direct query: task.routed -> Answer directly
        if ev_name == "task.routed":
            prompt = str(payload.get("objective") or payload.get("prompt") or payload.get("task_description") or "")
            task_type = payload.get("task_type")
            decision = await self.analyze_task(prompt, task_type=task_type)

            if not decision.get("selected_bot"):
                return EventResult.answer(
                    event,
                    response_payload=decision,
                    reason="NO_ELIGIBLE_BOT: No registered bot covers the required capabilities",
                )

            return EventResult.answer(
                event,
                response_payload=decision,
                reason=f"ROUTED: Selected @{decision['selected_bot']} ({decision['selected_bot_role']})",
            )

        # 2. Task/Run admission: run.admit, task.admit, bot.task_assigned
        if ev_name in ("run.admit", "task.admit", "bot.task_assigned", "agent.spawn_requested"):
            assigned_bot = payload.get("bot_name") or payload.get("agent")
            reg = self.registry

            # Case A: A specific bot was already requested -> Validate it
            if assigned_bot:
                bot_key = str(assigned_bot).lower().strip()
                profile = reg.get_bot(bot_key)

                if profile is None:
                    # Let unknown bots pass through if they are dynamic agents or leader, but check if on roster
                    if bot_key not in ("alpha", "lead", "supervisor", "operator"):
                        logger.warning("TaskRouterMod: requested bot '%s' not found on roster", bot_key)

                elif profile.status in ("suspended", "archived"):
                    return EventResult.deny(
                        event,
                        reason=f"BOT_UNAVAILABLE: Bot '@{bot_key}' is {profile.status} and cannot be assigned tasks",
                    )
                elif profile.status == "sleeping":
                    # Automatically wake sleeping bot
                    reg.update_bot(bot_key, status="active", bump_version=False)

                return await next_fn(event)

            # Case B: No bot specified -> Automatically route and assign best bot
            prompt = str(payload.get("prompt") or payload.get("objective") or payload.get("message") or "")
            if prompt:
                decision = await self.analyze_task(prompt, task_type=payload.get("task_type"))
                if decision.get("selected_bot"):
                    best_bot = decision["selected_bot"]
                    mutated = event.with_payload(
                        bot_name=best_bot,
                        assigned_bot=best_bot,
                        model_category=decision.get("category"),
                        recommended_toolsets=decision.get("recommended_toolsets"),
                    )
                    return EventResult.rewrite(
                        mutated,
                        reason=f"TASK_ROUTER_ASSIGNMENT: Routed task to optimal specialist @{best_bot}",
                        metadata={"selected_bot": best_bot, "category": decision.get("category")},
                    )
                return EventResult.deny(
                    event,
                    reason="NO_ELIGIBLE_BOT: No registered bot covers the required capabilities",
                    metadata={"routing_decision": decision},
                )

        return await next_fn(event)
