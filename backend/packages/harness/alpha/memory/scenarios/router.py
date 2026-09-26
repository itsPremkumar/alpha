"""Scenario routing, budget enforcement, and per-thread plan freezing."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .classifier import ScenarioClassifier
from .config import ScenarioConfig, load_scenario_config
from .models import (
    DEFAULT_SCENARIO,
    ClassificationResult,
    RoutingPlan,
    Scenario,
    ScenarioSignals,
    SurfaceRoute,
    parse_scenario,
)
from .provenance import RoutingProvenance
from .registry import DEFAULT_REGISTRY, MemorySurfaceRegistry


class ScenarioRouter:
    """Route a scenario to registered surfaces under explicit budgets.

    The router has no store, network, or model-construction dependency.  Its
    collaborators are injected, so a test can use a small fake registry and a
    fake classifier/model while production can provide the default registry
    and a separately constructed model seam.
    """

    def __init__(
        self,
        registry: MemorySurfaceRegistry | None = None,
        classifier: ScenarioClassifier | None = None,
        config: ScenarioConfig | Mapping[str, Any] | None = None,
        *,
        model: Any | None = None,
        model_classifier: Any | None = None,
        provenance: RoutingProvenance | str | Path | Any | None = None,
        record_provenance: bool | None = None,
        user_id: str | None = None,
    ) -> None:
        if isinstance(config, Mapping):
            config = ScenarioConfig.model_validate(config)
        self.config = config if config is not None else load_scenario_config()
        self.registry = registry if registry is not None else DEFAULT_REGISTRY
        self.classifier = classifier or ScenarioClassifier(
            config=self.config,
            model=model if model is not None else model_classifier,
        )
        self.user_id = user_id
        self._thread_lock = threading.RLock()
        self._thread_plans: dict[str, RoutingPlan] = {}
        if isinstance(provenance, (str, Path)):
            self._provenance: Any | None = RoutingProvenance(provenance)
        else:
            self._provenance = provenance
        if record_provenance is not False and self._provenance is None:
            # Provenance is part of the subsystem contract.  Tests/embedders
            # that explicitly pass ``record_provenance=False`` can avoid disk
            # I/O; normal routers retain a per-day decision record by default.
            self._provenance = RoutingProvenance(self.config.storage_path)

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    def _disabled_plan(self) -> RoutingPlan:
        return RoutingPlan(
            scenario=DEFAULT_SCENARIO,
            confidence=0.0,
            surfaces=[],
            total_budget_units=0,
            reason="scenario-conditioned recall is disabled by configuration",
            status="disabled",
            enabled=False,
            budget_limit_units=0,
        )

    def _normalise_signals(self, signals: ScenarioSignals | Mapping[str, Any] | None) -> ScenarioSignals:
        if isinstance(signals, ScenarioSignals):
            return signals
        return ScenarioSignals.model_validate(signals or {})

    def _fallback_scenario(self) -> Scenario:
        candidate = getattr(self.classifier, "fallback_scenario", DEFAULT_SCENARIO)
        return parse_scenario(candidate, default=DEFAULT_SCENARIO) or DEFAULT_SCENARIO

    def _record(self, plan: RoutingPlan, *, thread_id: str | None) -> None:
        if self._provenance is None:
            return
        try:
            if hasattr(self._provenance, "append"):
                self._provenance.append(plan, user_id=self.user_id, thread_id=thread_id)
            elif callable(self._provenance):
                self._provenance(plan)
        except Exception:  # noqa: BLE001 - audit logging must not break recall
            return

    def route(
        self,
        signals: ScenarioSignals | Mapping[str, Any] | None = None,
        budget_units: int | None = None,
        *,
        thread_id: str | None = None,
        explicit_override: Scenario | str | None = None,
    ) -> RoutingPlan:
        """Return a deterministic routing plan for the current signals."""

        if explicit_override is not None:
            if signals is None:
                signals = ScenarioSignals(explicit_override=explicit_override)
            elif isinstance(signals, ScenarioSignals):
                override_value = explicit_override.value if isinstance(explicit_override, Scenario) else explicit_override
                signals = signals.model_copy(update={"explicit_override": override_value})
            elif isinstance(signals, Mapping):
                signals = {**dict(signals), "explicit_override": explicit_override}
        # This explicit gate is intentionally the first operation: a disabled
        # subsystem must not classify, read a registry, or write provenance.
        if not self.config.enabled:
            return self._disabled_plan()
        fallback = self._fallback_scenario()
        fallback_name = fallback.value
        try:
            normalized = self._normalise_signals(signals)
        except Exception as exc:  # noqa: BLE001 - malformed signals fail closed
            classification = ClassificationResult(
                scenario=fallback,
                confidence=0.0,
                status="no_signal",
                reason=f"invalid signals; defaulted to {fallback_name} ({str(exc)[:160]})",
            )
        else:
            try:
                classification = self.classifier.classify(normalized)
            except Exception as exc:  # noqa: BLE001 - classifier seam is fail-safe
                classification = ClassificationResult(
                    scenario=fallback,
                    confidence=0.0,
                    status="model_error",
                    reason=f"classifier failure; defaulted to {fallback_name} ({str(exc)[:160]})",
                )

        scenario = classification.scenario if classification.scenario in tuple(Scenario) else DEFAULT_SCENARIO
        try:
            candidates = self.registry.list(scenario)
        except Exception as exc:  # noqa: BLE001 - a registry failure discloses an empty plan
            candidates = []
            classification.reason = f"{classification.reason}; registry unavailable ({str(exc)[:120]})"

        limit = self.config.total_budget_units
        disclosures: list[str] = []
        if budget_units is not None:
            try:
                requested = int(budget_units)
            except (TypeError, ValueError, OverflowError):
                requested = 0
                disclosures.append("invalid budget_units replaced with 0")
            if requested < 0:
                requested = 0
                disclosures.append("negative budget_units clamped to 0")
            limit = min(limit, requested)
        if limit < 0:
            limit = 0
            disclosures.append("negative total_budget_units clamped to 0")

        ordered = sorted(candidates, key=lambda surface: (-float(surface.base_weight), surface.name))
        selected: list[SurfaceRoute] = []
        omitted: list[str] = []
        used = 0
        for surface in ordered:
            if len(selected) >= self.config.max_surfaces:
                omitted.append(surface.name)
                continue
            cost = max(1, int(surface.cost_units))
            if used + cost > limit:
                omitted.append(surface.name)
                continue
            selected.append(
                SurfaceRoute(
                    name=surface.name,
                    weight=float(surface.base_weight),
                    budget_units=cost,
                )
            )
            used += cost

        selected_names = ", ".join(route.name for route in selected) or "none"
        omitted_text = f"; omitted by budget/limit: {', '.join(omitted)}" if omitted else ""
        cap_text = f"; budget limit {limit} units" if budget_units is not None else ""
        reason = f"{classification.reason}; selected surfaces: {selected_names}{cap_text}{omitted_text}"
        if disclosures:
            reason += "; " + "; ".join(disclosures)
        plan = RoutingPlan(
            scenario=scenario,
            confidence=classification.confidence,
            surfaces=selected,
            total_budget_units=used,
            reason=reason,
            status=classification.status,
            enabled=True,
            budget_limit_units=limit,
            omitted_surfaces=omitted,
            disclosures=disclosures,
        )
        self._record(plan, thread_id=thread_id)
        return plan

    def plan_for_thread(
        self,
        thread_id: str,
        signals: ScenarioSignals | Mapping[str, Any] | None = None,
        *,
        budget_units: int | None = None,
        explicit_override: Scenario | str | None = None,
    ) -> RoutingPlan:
        """Return a thread-frozen plan, with valid explicit override bypass."""

        if explicit_override is not None:
            if signals is None:
                signals = ScenarioSignals(explicit_override=explicit_override)
            elif isinstance(signals, ScenarioSignals):
                signals = signals.model_copy(update={"explicit_override": str(Scenario(explicit_override)) if isinstance(explicit_override, Scenario) else explicit_override})
            elif isinstance(signals, Mapping):
                signals = {**dict(signals), "explicit_override": explicit_override}
        key = str(thread_id or "default")
        if not self.config.session_freeze:
            return self.route(signals, budget_units, thread_id=key)
        explicit = False
        if signals is not None:
            try:
                normalized = self._normalise_signals(signals)
                explicit = normalized.explicit_override is not None and parse_scenario(normalized.explicit_override, default=None) is not None
            except Exception:  # noqa: BLE001 - malformed override cannot unfreeze
                explicit = False
        with self._thread_lock:
            cached = self._thread_plans.get(key)
            if cached is not None and not (explicit and self.config.override_env_freeze):
                return cached
            plan = self.route(signals, budget_units, thread_id=key)
            self._thread_plans[key] = plan
            return plan

    def clear_thread(self, thread_id: str) -> bool:
        with self._thread_lock:
            return self._thread_plans.pop(str(thread_id or "default"), None) is not None

    def clear(self) -> None:
        with self._thread_lock:
            self._thread_plans.clear()

    def register(self, surface: Any, **fields: Any) -> Any:
        return self.registry.register(surface, **fields)

    def unregister(self, name: str) -> bool:
        return self.registry.unregister(name)

    def explain(self, plan: RoutingPlan) -> str:
        """Return a stable human-readable explanation for logs and tests."""

        if not isinstance(plan, RoutingPlan):
            plan = RoutingPlan.model_validate(plan)
        reason = plan.reason or "no deciding reason recorded"
        names = ", ".join(plan.surface_names) or "none"
        return f"{reason} (scenario={plan.scenario.value}; confidence={plan.confidence:.2f}; surfaces={names})"

    def describe(self) -> Any:
        return self.registry.describe()


# Descriptive aliases make the seam easy to discover without coupling callers
# to one particular class spelling.
ScenarioMemoryRouter = ScenarioRouter
ScenarioRecallRouter = ScenarioRouter
ScenarioMemory = ScenarioRouter
ScenarioConditionedRecall = ScenarioRouter


def route(
    signals: ScenarioSignals | Mapping[str, Any] | None,
    budget_units: int | None = None,
    *,
    router: ScenarioRouter | None = None,
) -> RoutingPlan:
    """Functional convenience wrapper using an explicitly supplied router."""

    active = router or ScenarioRouter()
    return active.route(signals, budget_units)


def explain(plan: RoutingPlan) -> str:
    return (plan.reason if isinstance(plan, RoutingPlan) else str(plan)) or "no deciding reason recorded"


__all__ = [
    "ScenarioConditionedRecall",
    "ScenarioMemory",
    "ScenarioMemoryRouter",
    "ScenarioRecallRouter",
    "ScenarioRouter",
    "explain",
    "route",
]
