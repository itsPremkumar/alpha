"""Workforce Model and Cost Tier Router with Intelligent Fallback Chains.

Maps multi-agent tasks and bot roles to optimal cost and reasoning tiers
(Frontier, Coding, Fast, Local). Provides automatic degradation fallback chains
so work continues seamlessly across rate limits or quota exhausts.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    FRONTIER = "frontier"  # Deep reasoning, architecture, security, postmortem
    CODING = "coding"      # Code generation, refactoring, test synthesis
    FAST = "fast"          # Research, review, summary, docs, triage
    LOCAL = "local"        # Offline, air-gapped, zero-cost fallback


DEFAULT_TIER_MODELS: dict[ModelTier, list[str]] = {
    ModelTier.FRONTIER: ["claude-3-7-sonnet", "o3-mini", "gpt-4o", "ollama/qwen3:32b"],
    ModelTier.CODING: ["claude-3-5-sonnet", "deepseek-coder", "gpt-4o", "ollama/qwen3-coder:30b"],
    ModelTier.FAST: ["gpt-4o-mini", "gemini-2.0-flash", "claude-3-5-haiku", "ollama/llama3.1:8b"],
    ModelTier.LOCAL: ["ollama/qwen3-coder:30b", "ollama/llama3.1:8b", "local-default"],
}

TIER_COST_INDICATORS: dict[ModelTier, str] = {
    ModelTier.FRONTIER: "$$$",
    ModelTier.CODING: "$$",
    ModelTier.FAST: "$",
    ModelTier.LOCAL: "free",
}

ROLE_TIER_PREFERENCES: dict[str, ModelTier] = {
    "architect": ModelTier.FRONTIER,
    "security": ModelTier.FRONTIER,
    "lead": ModelTier.FRONTIER,
    "coder": ModelTier.CODING,
    "developer": ModelTier.CODING,
    "engineer": ModelTier.CODING,
    "tester": ModelTier.CODING,
    "reviewer": ModelTier.FAST,
    "researcher": ModelTier.FAST,
    "doc": ModelTier.FAST,
    "triage": ModelTier.FAST,
}

TASK_TIER_PREFERENCES: dict[str, ModelTier] = {
    "architecture": ModelTier.FRONTIER,
    "security": ModelTier.FRONTIER,
    "audit": ModelTier.FRONTIER,
    "postmortem": ModelTier.FRONTIER,
    "coding": ModelTier.CODING,
    "refactoring": ModelTier.CODING,
    "implementation": ModelTier.CODING,
    "bugfix": ModelTier.CODING,
    "research": ModelTier.FAST,
    "review": ModelTier.FAST,
    "docs": ModelTier.FAST,
    "summary": ModelTier.FAST,
    "triage": ModelTier.FAST,
}


@dataclass
class ModelRouteDecision:
    """Outcome of model routing including primary selection and fallback chain."""

    tier: str
    primary_model: str
    fallback_chain: list[str] = field(default_factory=list)
    cost_tier: str = "$"
    reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkforceModelRouter:
    """Thread-safe model and cost router with configurable tiers and fallback chains."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tier_models: dict[ModelTier, list[str]] = {
            k: list(v) for k, v in DEFAULT_TIER_MODELS.items()
        }

    def set_tier_models(self, tier: ModelTier | str, models: list[str]) -> None:
        t = ModelTier(tier) if isinstance(tier, str) else tier
        with self._lock:
            self._tier_models[t] = list(models)

    def get_tier_models(self, tier: ModelTier | str) -> list[str]:
        t = ModelTier(tier) if isinstance(tier, str) else tier
        with self._lock:
            return list(self._tier_models.get(t, []))

    def route_model(
        self,
        task_category: str = "",
        *,
        bot_role: str | None = None,
        complexity: str = "medium",
        prefer_local: bool = False,
    ) -> ModelRouteDecision:
        """Determine optimal tier and model chain based on task, bot role, and complexity."""
        cat_clean = task_category.lower().strip()
        role_clean = (bot_role or "").lower().strip()

        # Step 1: Infer base tier
        selected_tier = ModelTier.FAST
        reason = "Default to fast/economical tier."

        if cat_clean in TASK_TIER_PREFERENCES:
            selected_tier = TASK_TIER_PREFERENCES[cat_clean]
            reason = f"Matched task category '{cat_clean}' to {selected_tier.value} tier."
        elif role_clean:
            for r_key, t_val in ROLE_TIER_PREFERENCES.items():
                if r_key in role_clean:
                    selected_tier = t_val
                    reason = f"Matched bot role '{bot_role}' to {selected_tier.value} tier."
                    break

        # Step 2: Complexity adjustments
        if complexity.lower() == "high":
            if selected_tier == ModelTier.FAST:
                selected_tier = ModelTier.CODING
                reason += " (Upgraded to coding tier due to high complexity)"
            elif selected_tier == ModelTier.CODING:
                selected_tier = ModelTier.FRONTIER
                reason += " (Upgraded to frontier tier due to high complexity)"
        elif complexity.lower() == "low" and selected_tier == ModelTier.CODING:
            selected_tier = ModelTier.FAST
            reason += " (Downgraded to fast tier due to low complexity)"

        # Step 3: Local preference
        if prefer_local:
            selected_tier = ModelTier.LOCAL
            reason += " (Local-only mode active)"

        # Step 4: Resolve models and fallback chain
        with self._lock:
            chain = list(self._tier_models.get(selected_tier, []))
            # Append local tier fallback if not already present
            if selected_tier != ModelTier.LOCAL:
                for local_m in self._tier_models.get(ModelTier.LOCAL, []):
                    if local_m not in chain:
                        chain.append(local_m)

        primary = chain[0] if chain else "default"
        fallbacks = chain[1:]

        return ModelRouteDecision(
            tier=selected_tier.value,
            primary_model=primary,
            fallback_chain=fallbacks,
            cost_tier=TIER_COST_INDICATORS.get(selected_tier, "$"),
            reasoning=reason,
        )

    def get_fallback_model(
        self,
        current_model: str,
        tier: ModelTier | str | None = None,
    ) -> str | None:
        """Find the next eligible fallback model after a failure or rate limit."""
        target_tier = (
            (ModelTier(tier) if isinstance(tier, str) else tier)
            if tier
            else None
        )

        with self._lock:
            candidates: list[str] = []
            if target_tier and target_tier in self._tier_models:
                candidates.extend(self._tier_models[target_tier])
            else:
                for models in self._tier_models.values():
                    candidates.extend(models)

            # Deduplicate while preserving order
            seen: set[str] = set()
            ordered: list[str] = []
            for m in candidates:
                if m not in seen:
                    seen.add(m)
                    ordered.append(m)

            if current_model in ordered:
                curr_idx = ordered.index(current_model)
                if curr_idx + 1 < len(ordered):
                    return ordered[curr_idx + 1]

            # Fallback to local default if exhausted
            local_chain = self._tier_models.get(ModelTier.LOCAL, [])
            for m in local_chain:
                if m != current_model:
                    return m

        return None


_workforce_router: WorkforceModelRouter | None = None
_router_lock = threading.Lock()


def get_workforce_model_router() -> WorkforceModelRouter:
    global _workforce_router
    with _router_lock:
        if _workforce_router is None:
            _workforce_router = WorkforceModelRouter()
        return _workforce_router
