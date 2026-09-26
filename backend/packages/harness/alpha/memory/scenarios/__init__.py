"""Scenario-conditioned recall for Alpha's composable memory surfaces.

This package implements the type listed as row 19 in
``docs/MEMORY_TYPES.md``: it chooses which already-registered memory surfaces
matter for the current kind of work, then applies weights and explicit budget
limits.  It owns no concrete memory store and imports no prospective,
affective, narrative, entity, or social subsystem.  Those owners register
metadata through :class:`MemorySurfaceRegistry` and provide their own blocks to
:func:`render`.

The package is additive and local: its default gate is off, its router accepts
injected signals/model seams, and its configuration does not read or mutate
``MemoryConfig``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "CLASSIFICATION_STATUSES": "classifier",
    "ClassificationOutcome": "models",
    "ClassificationResult": "models",
    "ClassificationStatus": "classifier",
    "ConfigLoadResult": "config",
    "DEFAULT_MAX_RENDER_CHARS": "recall",
    "DEFAULT_MEMORY_SURFACES": "registry",
    "DEFAULT_REGISTRY": "registry",
    "DEFAULT_SCENARIO": "models",
    "DEFAULT_SURFACES": "registry",
    "DuplicateSurfaceError": "registry",
    "KNOWN_CLASSIFICATION_STATUSES": "classifier",
    "KNOWN_MODEL_STATUSES": "classifier",
    "MODEL_STATUSES": "classifier",
    "MemorySurface": "models",
    "MemorySurfaceRegistry": "registry",
    "ModelClassificationStatus": "classifier",
    "ModelParseResult": "classifier",
    "RecallRenderer": "recall",
    "RecallStats": "recall",
    "RegistryDescription": "registry",
    "RoutedSurface": "models",
    "RoutingPlan": "models",
    "RoutingProvenance": "provenance",
    "SCENARIOS": "models",
    "Scenario": "models",
    "ScenarioClassifier": "classifier",
    "ScenarioConditionedRecall": "router",
    "ScenarioConfig": "config",
    "ScenarioConfigError": "config",
    "ScenarioMemory": "router",
    "ScenarioMemoryRouter": "router",
    "ScenarioRecall": "recall",
    "ScenarioRecallRenderer": "recall",
    "ScenarioRecallRouter": "router",
    "ScenarioRouter": "router",
    "ScenarioSignals": "models",
    "SurfaceNotFoundError": "registry",
    "SurfaceRoute": "models",
    "SurfaceSelection": "models",
    "append_decision": "provenance",
    "append_entry": "provenance",
    "append_routing_decision": "provenance",
    "append_routing_entry": "provenance",
    "build_default_registry": "registry",
    "classification_status_known": "classifier",
    "classify": "classifier",
    "classify_scenario": "classifier",
    "classify_signals": "classifier",
    "default_registry": "registry",
    "explain": "router",
    "get_default_registry": "registry",
    "get_surface_registry": "registry",
    "load_scenario_config": "config",
    "load_scenario_config_result": "config",
    "model_status_known": "classifier",
    "parse_classifier_response": "classifier",
    "parse_model_response": "classifier",
    "parse_scenario": "models",
    "provenance_path": "provenance",
    "read_entries": "provenance",
    "register_surface": "registry",
    "render": "recall",
    "render_plan": "recall",
    "reset_default_registry": "registry",
    "rewrite_day": "provenance",
    "route": "router",
    "scenario_enabled": "config",
    "scenario_root": "config",
    "scenarios_enabled": "config",
    "stats": "recall",
    "unregister_surface": "registry",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports: the runtime surface is installed
    # lazily above, so this block exists for type checkers and IDEs only.
    from .classifier import (
        CLASSIFICATION_STATUSES as CLASSIFICATION_STATUSES,
    )
    from .classifier import (
        KNOWN_CLASSIFICATION_STATUSES as KNOWN_CLASSIFICATION_STATUSES,
    )
    from .classifier import (
        KNOWN_MODEL_STATUSES as KNOWN_MODEL_STATUSES,
    )
    from .classifier import (
        MODEL_STATUSES as MODEL_STATUSES,
    )
    from .classifier import (
        ClassificationStatus as ClassificationStatus,
    )
    from .classifier import (
        ModelClassificationStatus as ModelClassificationStatus,
    )
    from .classifier import (
        ModelParseResult as ModelParseResult,
    )
    from .classifier import (
        ScenarioClassifier as ScenarioClassifier,
    )
    from .classifier import (
        classification_status_known as classification_status_known,
    )
    from .classifier import (
        classify as classify,
    )
    from .classifier import (
        classify_scenario as classify_scenario,
    )
    from .classifier import (
        classify_signals as classify_signals,
    )
    from .classifier import (
        model_status_known as model_status_known,
    )
    from .classifier import (
        parse_classifier_response as parse_classifier_response,
    )
    from .classifier import (
        parse_model_response as parse_model_response,
    )
    from .config import (
        ConfigLoadResult as ConfigLoadResult,
    )
    from .config import (
        ScenarioConfig as ScenarioConfig,
    )
    from .config import (
        ScenarioConfigError as ScenarioConfigError,
    )
    from .config import (
        load_scenario_config as load_scenario_config,
    )
    from .config import (
        load_scenario_config_result as load_scenario_config_result,
    )
    from .config import (
        scenario_enabled as scenario_enabled,
    )
    from .config import (
        scenario_root as scenario_root,
    )
    from .config import (
        scenarios_enabled as scenarios_enabled,
    )
    from .models import (
        DEFAULT_SCENARIO as DEFAULT_SCENARIO,
    )
    from .models import (
        SCENARIOS as SCENARIOS,
    )
    from .models import (
        ClassificationOutcome as ClassificationOutcome,
    )
    from .models import (
        ClassificationResult as ClassificationResult,
    )
    from .models import (
        MemorySurface as MemorySurface,
    )
    from .models import (
        RoutedSurface as RoutedSurface,
    )
    from .models import (
        RoutingPlan as RoutingPlan,
    )
    from .models import (
        Scenario as Scenario,
    )
    from .models import (
        ScenarioSignals as ScenarioSignals,
    )
    from .models import (
        SurfaceRoute as SurfaceRoute,
    )
    from .models import (
        SurfaceSelection as SurfaceSelection,
    )
    from .models import (
        parse_scenario as parse_scenario,
    )
    from .provenance import (
        RoutingProvenance as RoutingProvenance,
    )
    from .provenance import (
        append_decision as append_decision,
    )
    from .provenance import (
        append_entry as append_entry,
    )
    from .provenance import (
        append_routing_decision as append_routing_decision,
    )
    from .provenance import (
        append_routing_entry as append_routing_entry,
    )
    from .provenance import (
        provenance_path as provenance_path,
    )
    from .provenance import (
        read_entries as read_entries,
    )
    from .provenance import (
        rewrite_day as rewrite_day,
    )
    from .recall import (
        DEFAULT_MAX_RENDER_CHARS as DEFAULT_MAX_RENDER_CHARS,
    )
    from .recall import (
        RecallRenderer as RecallRenderer,
    )
    from .recall import (
        RecallStats as RecallStats,
    )
    from .recall import (
        ScenarioRecall as ScenarioRecall,
    )
    from .recall import (
        ScenarioRecallRenderer as ScenarioRecallRenderer,
    )
    from .recall import (
        render as render,
    )
    from .recall import (
        render_plan as render_plan,
    )
    from .recall import (
        stats as stats,
    )
    from .registry import (
        DEFAULT_MEMORY_SURFACES as DEFAULT_MEMORY_SURFACES,
    )
    from .registry import (
        DEFAULT_REGISTRY as DEFAULT_REGISTRY,
    )
    from .registry import (
        DEFAULT_SURFACES as DEFAULT_SURFACES,
    )
    from .registry import (
        DuplicateSurfaceError as DuplicateSurfaceError,
    )
    from .registry import (
        MemorySurfaceRegistry as MemorySurfaceRegistry,
    )
    from .registry import (
        RegistryDescription as RegistryDescription,
    )
    from .registry import (
        SurfaceNotFoundError as SurfaceNotFoundError,
    )
    from .registry import (
        build_default_registry as build_default_registry,
    )
    from .registry import (
        default_registry as default_registry,
    )
    from .registry import (
        get_default_registry as get_default_registry,
    )
    from .registry import (
        get_surface_registry as get_surface_registry,
    )
    from .registry import (
        register_surface as register_surface,
    )
    from .registry import (
        reset_default_registry as reset_default_registry,
    )
    from .registry import (
        unregister_surface as unregister_surface,
    )
    from .router import (
        ScenarioConditionedRecall as ScenarioConditionedRecall,
    )
    from .router import (
        ScenarioMemory as ScenarioMemory,
    )
    from .router import (
        ScenarioMemoryRouter as ScenarioMemoryRouter,
    )
    from .router import (
        ScenarioRecallRouter as ScenarioRecallRouter,
    )
    from .router import (
        ScenarioRouter as ScenarioRouter,
    )
    from .router import (
        explain as explain,
    )
    from .router import (
        route as route,
    )

install_lazy_exports(__name__, _EXPORTS)
