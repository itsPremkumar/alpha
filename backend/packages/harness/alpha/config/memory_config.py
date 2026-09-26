"""Configuration for the memory mechanism (host-shared fields only).

DeerMem-private fields live in ``backends/deermem/config.py`` (``DeerMemConfig``),
reached via ``backend_config`` (a dict the factory passes to the backend's
``__init__``). This module holds ONLY the host-shared fields every backend /
call site / factory reads: ``enabled`` / ``injection_enabled`` /
``shutdown_flush_timeout_seconds`` / ``manager_class`` / ``backend_config`` /
``user_model`` / ``l1`` (the additive typed-memory pipeline sub-config) and the
per-type sub-configs for the additive memory subsystems (``affective``,
``entities``, ``fusion``, ``narrative``, ``policy``, ``prospective``,
``social``). Each of those types is default-OFF and owns its storage; the
shared schema only decides whether the host may construct it. Those packages
expose their config models with lazy package exports, so importing them here
keeps the import graph acyclic.
Keeping the shared schema slim is what
makes backends swappable and portable (DeerMem's knobs do not leak onto the
shared contract).
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Additive per-type memory subsystem configs. Each package default is OFF, so
# an existing deployment changes nothing until the operator enables the type.
from alpha.memory.affective.config import AffectiveConfig
from alpha.memory.codebase.config import CodebaseConfig
from alpha.memory.entities.config import EntityConfig
from alpha.memory.evaluation.config import EvaluationConfig
from alpha.memory.fabric.config import FabricConfig
from alpha.memory.fusion.config import FusionConfig
from alpha.memory.health.config import HealthConfig
from alpha.memory.narrative.config import NarrativeConfig
from alpha.memory.policy.config import PolicyConfig
from alpha.memory.prospective.config import ProspectiveConfig
from alpha.memory.scenarios.config import ScenarioConfig
from alpha.memory.social.config import SocialConfig
from alpha.memory.utility.config import UtilityConfig

logger = logging.getLogger(__name__)

# Host-shared MemoryConfig fields (read by every backend / call site / factory).
_SHARED_FIELDS = frozenset(
    {
        "enabled",
        "mode",
        "injection_enabled",
        "shutdown_flush_timeout_seconds",
        "manager_class",
        "backend_config",
        "user_model",
        "l1",
        "affective",
        "codebase",
        "entities",
        "evaluation",
        "fabric",
        "fusion",
        "health",
        "narrative",
        "policy",
        "prospective",
        "scenarios",
        "social",
        "utility",
    }
)

# DeerMem-private fields that used to live at the top level of `memory:` in
# config.yaml (pre-abstraction). On load they are auto-migrated into
# `backend_config` so an upgrade does NOT silently revert customized settings
# to defaults. `model_name` maps to `backend_config.model.model` (the new nested
# model sub-config); the rest are 1:1.
_LEGACY_DEERMEM_FIELDS = frozenset(
    {
        "storage_path",
        "storage_class",
        "debounce_seconds",
        "max_facts",
        "fact_confidence_threshold",
        "max_injection_tokens",
        "token_counting",
        "guaranteed_categories",
        "guaranteed_token_budget",
        "staleness_review_enabled",
        "staleness_age_days",
        "staleness_min_candidates",
        "staleness_max_removals_per_cycle",
        "staleness_protected_categories",
        "staleness_max_lifetime_multiplier",
        "staleness_max_extension_days",
        "consolidation_enabled",
        "consolidation_min_facts",
        "consolidation_max_groups_per_cycle",
        "consolidation_max_sources",
        "model_name",
    }
)


class MemoryConfig(BaseModel):
    """Host-shared memory configuration (backend-agnostic)."""

    enabled: bool = Field(
        default=True,
        description="Whether to enable the memory mechanism (call-site gate).",
    )
    mode: Literal["middleware", "tool"] = Field(
        default="middleware",
        description=(
            "Memory operation mode. 'middleware': passive LLM summarization after each turn (current behavior). 'tool': model calls memory tools (memory_search, memory_add, etc.) directly. Mutually exclusive — only one mode runs at a time."
        ),
    )
    injection_enabled: bool = Field(
        default=True,
        description="Whether to inject memory into the system prompt (call-site gate).",
    )
    shutdown_flush_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description=(
            "Hard time budget (seconds) for draining the memory backend's "
            "pending-update buffer during Gateway graceful shutdown. The drain "
            "makes one LLM call per pending item, so large IM batches may need "
            "a higher value. Must fit inside the pod's K8s "
            "terminationGracePeriodSeconds (together with channel/scheduler "
            "stop) or K8s SIGKILLs the drain mid-flight. The drain runs on a "
            "daemon thread, so on timeout the process proceeds to exit and any "
            "unfinished tail is dropped (same failure direction as no flush, "
            "scoped to the tail). Host-shared (not backend-private): the host "
            "owns the lifespan budget and the K8s grace relationship."
        ),
    )
    manager_class: str = Field(
        default="deermem",
        description=(
            "Memory backend selector. Either a registered backend name "
            "(matching a `backends/<name>/` folder that exposes `MANAGER_CLASS`, "
            "e.g. `deermem` / `noop`) or a dotted import path to a "
            "`MemoryManager` subclass. The factory resolves this at "
            "`get_memory_manager()` time and raises `ValueError` on failure "
            "(fail-fast: memory is persistent state, so an unresolved "
            "manager_class is not silently substituted with a different "
            "storage backend)."
        ),
    )
    backend_config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Backend-private config (a dict), passed verbatim to the backend's "
            "`__init__(backend_config=...)` by the factory. Each backend "
            "self-interprets it (DeerMem parses it into `DeerMemConfig`). Values "
            "live in the host config file (`config.yaml` `memory.backend_config`); "
            "they do not belong on the shared `MemoryConfig` schema."
        ),
    )
    user_model: "UserModelConfig" = Field(
        default_factory=lambda: UserModelConfig(),
        description="User-model provider configuration for personalized context injection.",
    )
    l1: "L1MemoryConfig" = Field(
        default_factory=lambda: L1MemoryConfig(),
        description=(
            "L1 typed-memory pipeline (scene segmentation + typed extraction + dedup/merge + quota + provenance + retention + recall). Additive on top of the existing backend behavior; gated by both memory.enabled and memory.l1.enabled."
        ),
    )
    # --- Additive memory subsystems. Each is default-OFF, owns its own
    # per-user storage, and composes at the recall seam. Enabling one never
    # changes the configured backend's behavior; disabling one is a no-op.
    affective: AffectiveConfig = Field(
        default_factory=AffectiveConfig,
        description=(
            "Affective memory: remembered emotional tone (valence/arousal events), "
            "decay-weighted mood state, and mood-aware recall. Requires "
            "memory.enabled AND memory.affective.enabled."
        ),
    )
    entities: EntityConfig = Field(
        default_factory=EntityConfig,
        description=(
            "Entity memory: deterministic (+ optional model) entity extraction, "
            "alias resolution/merge, and entity-scoped recall. Requires "
            "memory.enabled AND memory.entities.enabled."
        ),
    )
    narrative: NarrativeConfig = Field(
        default_factory=NarrativeConfig,
        description=(
            "Narrative memory: an ordered, bounded life-story timeline synthesized "
            "from stored records/episodes. Requires memory.enabled AND "
            "memory.narrative.enabled."
        ),
    )
    prospective: ProspectiveConfig = Field(
        default_factory=ProspectiveConfig,
        description=(
            "Prospective memory: reminders, commitments, and trigger-bound "
            "obligations with a pending -> fired -> done/expired lifecycle. "
            "Requires memory.enabled AND memory.prospective.enabled."
        ),
    )
    social: SocialConfig = Field(
        default_factory=SocialConfig,
        description=(
            "Social/shared memory: counterparts, relationship state, and "
            "audience-scoped shared facts behind default-deny grants. Requires "
            "memory.enabled AND memory.social.enabled."
        ),
    )
    policy: PolicyConfig = Field(
        default_factory=PolicyConfig,
        description=(
            "Memory admission policy: weighted admission score, hard rules "
            "(including secret rejection), and hot-reloadable policy documents "
            "that fail closed. Requires memory.enabled AND memory.policy.enabled."
        ),
    )
    fusion: FusionConfig = Field(
        default_factory=FusionConfig,
        description=(
            "Retrieval fusion + context composition: multi-stage retrieval "
            "(exact/semantic/graph/temporal/procedural), weighted fusion with "
            "optional RRF, contradiction/MMR handling, and per-memory-type token "
            "budgets. Requires memory.enabled AND memory.fusion.enabled."
        ),
    )
    scenarios: ScenarioConfig = Field(
        default_factory=ScenarioConfig,
        description=(
            "Scenario-conditioned recall routing: classify the current kind of "
            "work, then select/weight/budget the registered memory surfaces that "
            "matter for it. Requires memory.enabled AND memory.scenarios.enabled."
        ),
    )
    fabric: FabricConfig = Field(
        default_factory=FabricConfig,
        description=(
            "Memory fabric: canonical envelopes shared by every memory type, "
            "with namespace isolation, lifecycle transitions (compress/archive/"
            "promote/purge), temporal validity, fail-closed secret "
            "classification, and forget/restore. Requires memory.enabled AND "
            "memory.fabric.enabled."
        ),
    )
    evaluation: EvaluationConfig = Field(
        default_factory=EvaluationConfig,
        description=(
            "Opt-in memory benchmark: measured extraction recall, multi-session/"
            "temporal/update accuracy, abstention, contamination, write "
            "precision, evidence traceability and token efficiency against "
            "explicit thresholds. Off by default; a run is an operator action, "
            "never a unit-test side effect."
        ),
    )
    codebase: CodebaseConfig = Field(
        default_factory=CodebaseConfig,
        description=(
            "Codebase structure memory: a bounded, incrementally refreshed index of "
            "modules, symbols and dependency edges, with cycle-safe transitive impact "
            "analysis and disclosed skipped files. It parses source with the standard "
            "library and never executes or imports the analysed project. Requires "
            "memory.enabled AND memory.codebase.enabled."
        ),
    )
    health: HealthConfig = Field(
        default_factory=HealthConfig,
        description=(
            "Opt-in memory health and observability plane: dependency-injected "
            "component health, bounded metric reservoirs with overflow "
            "disclosure, and SLO evaluation where insufficient data is a "
            "distinct, non-passing outcome. Requires memory.enabled AND "
            "memory.health.enabled."
        ),
    )
    utility: UtilityConfig = Field(
        default_factory=UtilityConfig,
        description=(
            "Opt-in memory utility feedback plane: learns which stored memories "
            "earn their space from observed feedback, and proposes (never "
            "performs) retention and dedup actions. Scores stay labelled "
            "heuristic until calibrated with enough samples. Requires "
            "memory.enabled AND memory.utility.enabled."
        ),
    )


class L1MemoryConfig(BaseModel):
    """L1 typed-memory pipeline settings (``memory.l1``).

    Adapted from TencentDB-Agent-Memory ``MemoryCore/src/core/`` (MIT; see
    ``docs/THIRD_PARTY_MEMORY_NOTICES.md``). Every field is read by the L1
    pipeline, the recall seam, or the middleware registration gate.

    The pipeline shares the master ``memory.enabled`` gate: nothing runs
    unless BOTH ``memory.enabled`` and ``memory.l1.enabled`` are true. The
    default here is ``False`` so existing embedders and the hermetic test
    suite keep their exact current behavior; ``config.yaml`` ships
    ``l1.enabled: true`` so the feature is live in the product.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Master switch for the L1 typed-memory pipeline. Also requires memory.enabled.",
    )
    mode: Literal["chat", "work"] = Field(
        default="chat",
        description="Extraction/conflict prompt dialect: 'chat' (personal) or 'work' (team shared memory).",
    )
    extraction_model: str | None = Field(
        default=None,
        description="Chat model name for extraction/dedup/persona calls. None uses the host default model.",
    )
    debounce_seconds: float = Field(
        default=5.0,
        ge=0.0,
        le=600.0,
        description="Wait after a capture before extraction runs (batches rapid turns). 0 runs on the next worker tick.",
    )
    max_memories_per_run: int = Field(
        default=12,
        ge=1,
        le=200,
        description="Upper bound of extracted memories accepted from one extraction call (parser clamps).",
    )
    min_priority: int = Field(
        default=60,
        ge=-1,
        le=100,
        description="Extracted memories below this priority are dropped, except -1 (strict-instruction sentinel).",
    )
    dedup_enabled: bool = Field(
        default=True,
        description="Run batch conflict detection (store/skip/update/merge) before writing extracted memories.",
    )
    dedup_top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Existing candidate memories recalled per new memory for conflict detection.",
    )
    quota_enabled: bool = Field(
        default=True,
        description="Enforce per-user record and credit quotas before runs and before writes land.",
    )
    quota_memory_limit: int = Field(
        default=10000,
        ge=1,
        description="Maximum stored L1 records per user; writes beyond the limit are refused and disclosed.",
    )
    quota_credit_limit: float = Field(
        default=1000.0,
        gt=0.0,
        description="Maximum cumulative extraction credits per user; runs beyond the limit are skipped and disclosed.",
    )
    provenance_enabled: bool = Field(
        default=True,
        description="Append a generation-log record for every pipeline run under the L1 store.",
    )
    retention_enabled: bool = Field(
        default=True,
        description="Apply the retention sweep after each run.",
    )
    retention_max_records: int = Field(
        default=5000,
        ge=1,
        description="Retention sweep drops lowest-priority oldest records beyond this count.",
    )
    retention_max_age_days: int = Field(
        default=180,
        ge=1,
        description="Retention sweep drops non-pinned records older than this many days.",
    )
    recall_enabled: bool = Field(
        default=True,
        description="Inject the top L1 records into the <memory> block at recall time.",
    )
    recall_top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        description="How many L1 records the recall seam injects.",
    )
    persona_enabled: bool = Field(
        default=True,
        description="Synthesize a persona profile from stored persona-type L1 records after runs with new persona memories.",
    )
    persona_min_memories: int = Field(
        default=3,
        ge=1,
        description="Minimum persona-type records required before persona synthesis runs.",
    )
    storage_path: str | None = Field(
        default=None,
        description="Root directory for L1 records/logs. None resolves to the agent runtime home (per-user layout).",
    )


class UserModelConfig(BaseModel):
    """Configuration for the user-model provider."""

    provider: str | None = Field(
        default=None,
        description="Provider name (e.g., 'file', 'null'). None or 'null' uses NullUserModelProvider (byte-identical prompts).",
    )
    storage_path: str | None = Field(
        default=None,
        description="Storage path for file-backed provider. Required when provider='file'.",
    )

    model_config = ConfigDict(extra="forbid")


# Resolve the forward references used by MemoryConfig after both nested models
# have been declared. This keeps the module importable while preserving the
# declarative Pydantic schema.
MemoryConfig.model_rebuild()


def should_use_memory_tools(config: MemoryConfig) -> bool:
    """Return True when memory should use model-directed tools."""
    return config.enabled and config.mode == "tool"


# Global configuration instance
_memory_config: MemoryConfig = MemoryConfig()


def get_memory_config() -> MemoryConfig:
    """Get the current memory configuration.

    ``_memory_config`` is only refreshed as a side effect of ``get_app_config()``
    reloading (via ``_apply_singleton_configs`` -> ``load_memory_config_from_dict``).
    A reader that reaches memory config without going through ``get_app_config()``
    first -- e.g. the agent factory deciding whether to bind the memory tools --
    would otherwise see a stale ``memory.mode`` after a ``config.yaml`` edit, even
    though ``memory.*`` is documented as hot-reloadable. Trigger the same
    signature-checked reload here so the singleton follows the config file.

    If ``get_app_config()`` has never been called (``_app_config`` is ``None``),
    there is no stale config to refresh, so we keep the pre-existing behaviour
    of returning the in-memory singleton.  This avoids picking up a config file
    as a side effect of the first access to ``get_memory_config()``, which would
    break callers that expect module-level defaults (e.g. unit tests).
    """
    # Lazy import: app_config imports this module, so a top-level import cycles.
    from .app_config import _app_config, get_app_config

    if _app_config is not None:
        try:
            get_app_config()
        except Exception:
            # If the config file is transiently broken (invalid YAML, schema
            # violation, missing env var, etc.), keep the last-good singleton
            # so an in-flight turn completes normally instead of crashing.
            logger.warning(
                "Failed to reload app config from get_memory_config(); falling back to cached memory config.",
                exc_info=True,
            )
    return _memory_config


def set_memory_config(config: MemoryConfig) -> None:
    """Set the memory configuration."""
    global _memory_config
    _memory_config = config


def load_memory_config_from_dict(config_dict: dict) -> None:
    """Load memory configuration from a dictionary.

    Host-shared fields (``enabled`` / ``mode`` / ``injection_enabled`` /
    ``manager_class`` / ``backend_config``) are read directly. DeerMem-private
    fields that used to live at the top level of ``memory:`` in config.yaml
    (pre-abstraction: ``storage_path``, ``max_facts``, ``debounce_seconds``,
    ``model_name``, ``token_counting``, ``staleness_*``, ``consolidation_*``,
    ...) are **auto-migrated into ``backend_config``** with a warning, so an
    upgrade from a pre-abstraction config does NOT silently revert customized
    settings to defaults. Unknown top-level keys (likely typos) are warned and
    ignored.
    """
    global _memory_config
    config_dict = dict(config_dict or {})
    backend_config = dict(config_dict.get("backend_config") or {})
    migrated: list[str] = []
    for key in list(config_dict.keys()):
        if key in _SHARED_FIELDS:
            continue
        if key in _LEGACY_DEERMEM_FIELDS:
            value = config_dict.pop(key)
            if value is None or value == "":
                continue  # default / empty value, no migration needed
            if key == "model_name":
                # old top-level model_name -> backend_config.model.model
                model_cfg = dict(backend_config.get("model") or {})
                if "model" not in model_cfg:
                    model_cfg["model"] = value
                    backend_config["model"] = model_cfg
                    migrated.append(f"{key} -> backend_config.model.model")
            elif key == "storage_path" and str(value).endswith(".json"):
                # Pre-abstraction storage_path was a FILE path (absolute = shared
                # file opting out of per-user; a relative value like the old default
                # "memory.json" was ignored for per-user). DeerMem now treats it as a
                # root DIRECTORY. Carrying a file-style value verbatim would be
                # resolved as a dir and either orphan per-user memory or hit
                # NotADirectoryError on save. Drop it so the factory's zero-config
                # runtime_home kicks in (per-user location unchanged:
                # {base_dir}/users/{uid}/memory.json) and warn the operator.
                logger.warning(
                    "Legacy memory.storage_path=%r looks like a file path; DeerMem now "
                    "treats storage_path as a root DIRECTORY (per-user memory under "
                    "{storage_path}/users/{uid}/memory.json). Dropped -- memory now "
                    "lands under the default root (runtime_home). Set "
                    "memory.backend_config.storage_path to a directory if you want a "
                    "custom location.",
                    value,
                )
            elif key not in backend_config:
                # don't override an explicit backend_config value
                backend_config[key] = value
                migrated.append(f"{key} -> backend_config.{key}")
        else:
            logger.warning(
                "Unknown memory config key %r at top level (not a shared field %s nor a known legacy DeerMem field); ignored.",
                key,
                sorted(_SHARED_FIELDS),
            )
    if migrated:
        logger.warning(
            "Migrated legacy top-level memory fields into backend_config; move them under memory.backend_config in config.yaml to silence this: %s",
            ", ".join(migrated),
        )
    config_dict["backend_config"] = backend_config
    _memory_config = MemoryConfig(**config_dict)
