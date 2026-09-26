import logging
import os
import threading
from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Final, Literal, Self

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from alpha.config.acp_config import ACPAgentConfig, load_acp_config_from_dict
from alpha.config.agent_preset_config import AgentPresetConfig
from alpha.config.agent_storage_config import AgentStorageConfig
from alpha.config.agents_api_config import AgentsApiConfig, load_agents_api_config_from_dict
from alpha.config.auth_config import AuthAppConfig
from alpha.config.authorization_config import AuthorizationConfig, load_authorization_config_from_dict
from alpha.config.autonomy_config import AutonomyConfig
from alpha.config.capabilities_config import CapabilitiesConfig
from alpha.config.channel_connections_config import ChannelConnectionsConfig
from alpha.config.checkpointer_config import CheckpointerConfig, load_checkpointer_config_from_dict
from alpha.config.database_config import DatabaseConfig
from alpha.config.dedupe_storage_config import DedupeStorageConfig
from alpha.config.extensions_config import ExtensionsConfig
from alpha.config.file_signature import ConfigSignature as _ConfigSignature
from alpha.config.file_signature import get_config_signature as _get_config_signature
from alpha.config.guardrails_config import GuardrailsConfig, load_guardrails_config_from_dict
from alpha.config.hooks_config import HooksConfig
from alpha.config.input_polish_config import InputPolishConfig
from alpha.config.loop_detection_config import LoopDetectionConfig
from alpha.config.mcp_tasks_config import McpTasksConfig
from alpha.config.memory_config import MemoryConfig, load_memory_config_from_dict
from alpha.config.model_config import ModelConfig, ProviderConfig
from alpha.config.read_before_write_config import ReadBeforeWriteConfig
from alpha.config.reload_boundary import format_field_description
from alpha.config.review_guard_config import ReviewGuardConfig
from alpha.config.run_events_config import RunEventsConfig
from alpha.config.run_ownership_config import RunOwnershipConfig
from alpha.config.runtime_paths import existing_project_file
from alpha.config.safety_finish_reason_config import SafetyFinishReasonConfig
from alpha.config.sandbox_config import SandboxConfig
from alpha.config.scheduler_config import SchedulerConfig
from alpha.config.self_tuning.config import SelfTuningConfig
from alpha.config.skill_evolution_config import SkillEvolutionConfig
from alpha.config.skill_scan_config import SkillScanConfig
from alpha.config.skills_config import SkillsConfig
from alpha.config.stream_bridge_config import StreamBridgeConfig, load_stream_bridge_config_from_dict
from alpha.config.subagent_batches_config import SubagentBatchesConfig
from alpha.config.subagent_runtime_config import SubagentRuntimeConfig
from alpha.config.subagents_config import SubagentsAppConfig, load_subagents_config_from_dict
from alpha.config.suggestions_config import SuggestionsConfig
from alpha.config.summarization_config import SummarizationConfig, load_summarization_config_from_dict
from alpha.config.system_one_config import SystemOneConfig
from alpha.config.task_continuity_config import TaskContinuityConfig
from alpha.config.title_config import TitleConfig, load_title_config_from_dict
from alpha.config.token_budget_config import TokenBudgetConfig
from alpha.config.token_usage_config import TokenUsageConfig
from alpha.config.tool_config import ToolConfig, ToolGroupConfig
from alpha.config.tool_output_config import ToolOutputConfig
from alpha.config.tool_progress_config import ToolProgressConfig
from alpha.config.tool_search_config import ToolSearchConfig, load_tool_search_config_from_dict
from alpha.config.verification_config import VerificationConfig
from alpha.config.voice_config import VoiceConfig
from alpha.evolution.evidence.config import EvolutionEvidenceConfig
from alpha.extensions.loader import ExtensionSpec

load_dotenv()

logger = logging.getLogger(__name__)


CONFIG_FILE_DATABASE_DEFAULTS = {
    "backend": "sqlite",
    "sqlite_dir": ".agent-workspace/data",
}


# PyYAML ships two parsers: the pure-Python one and a libyaml-backed C one.
# ``yaml.safe_load`` always uses the pure-Python parser, which measured ~2.9 s
# on a 156 KB config.yaml here versus ~0.14 s for the C loader on the same
# bytes (identical result). config.yaml is parsed on every cold
# ``get_app_config()`` and config.example.yaml alongside it, so the choice of
# parser is the dominant cost of process startup — and of any first call that
# touches config from a fresh thread. Fall back transparently when libyaml is
# not compiled in (``pip install pyyaml`` without the C extension).
def _yaml_safe_load(stream: Any) -> Any:
    """Parse *stream* with the fastest safe YAML loader available."""
    c_loader = getattr(yaml, "CSafeLoader", None)
    if c_loader is not None:
        return yaml.load(stream, Loader=c_loader)
    return yaml.safe_load(stream)


class CircuitBreakerConfig(BaseModel):
    """Configuration for the LLM Circuit Breaker."""

    failure_threshold: int = Field(default=5, description="Number of consecutive failures before tripping the circuit")
    recovery_timeout_sec: int = Field(default=60, description="Time in seconds before attempting to recover the circuit")


class LlmCallConfig(BaseModel):
    """Configuration for LLM call execution (concurrency / rate shaping).

    Distinct from :class:`CircuitBreakerConfig` (which handles a *failing*
    provider) and from :class:`ModelConfig` (which describes model endpoints):
    these knobs shape how many LLM calls run at once and how the retry/backoff
    loop behaves. Capping concurrency caps the *slope* of the request rate,
    which is what a provider burst-rate (``limit_burst_rate``) limit fires on.
    """

    max_concurrent_calls: int = Field(
        default=0,
        ge=0,
        description=(
            "Process-wide cap on concurrently in-flight LLM calls. 0 disables "
            "the cap (default, preserving existing behavior). Set to a positive "
            "int to smooth provider burst-rate (limit_burst_rate) spikes by "
            "bounding the request-rate slope at the morning peak. Per-process, "
            "not per-cluster: with GATEWAY_WORKERS > 1 the aggregate cap is "
            "effectively max_concurrent_calls * GATEWAY_WORKERS (and a "
            "multi-node rollout multiplies it further), so size the per-process "
            "value accordingly and pair it with an nginx limit_req at the ingress "
            "for a true cluster-wide slope cap. Startup-only: the cap is captured "
            "at the first LLM run and frozen for the process lifetime, so editing "
            "it in config.yaml takes effect only after a gateway restart (the "
            "other llm_call.* knobs remain hot-reloadable). Freezing avoids the "
            "downscale/config-freshness races a runtime-mutable cap would "
            "introduce on a process-wide, cross-loop limiter."
        ),
    )
    retry_max_attempts: int = Field(
        default=3,
        ge=1,
        description="Max LLM call attempts (1 = no retry) for retriable transient errors.",
    )
    retry_base_delay_ms: int = Field(
        default=1000,
        ge=0,
        description="Base (ms) for the decorrelated-jitter retry backoff; seeds the first retry delay.",
    )
    retry_cap_delay_ms: int = Field(
        default=8000,
        ge=0,
        description="Hard cap (ms) on any single retry backoff delay.",
    )
    burst_retry_base_delay_ms: int = Field(
        default=5000,
        ge=0,
        description=(
            "Base (ms) for the backoff when the provider returns a burst-rate "
            "(limit_burst_rate) 429. Higher than retry_base_delay_ms so the "
            "single burst retry lands after the throttle window subsides. "
            "Ignored when the provider sends Retry-After (honored verbatim)."
        ),
    )


class LoggingEnhanceConfig(BaseModel):
    """Request trace logging enhancement settings.

    Trace ids are issued unconditionally (``TraceMiddleware`` for HTTP,
    ``ensure_trace_context`` elsewhere) and always returned in the
    ``X-Trace-Id`` response header. This block decides only whether log
    records carry that id, and in which format.
    """

    enabled: bool = Field(
        default=False,
        description="Print the request trace id into log records. Trace ids are always issued and always returned in the X-Trace-Id response header; this controls log output only.",
    )
    format: Literal["text", "json"] = Field(default="text", description="Enhanced log output format.")


class LoggingConfig(BaseModel):
    """Logging configuration."""

    enhance: LoggingEnhanceConfig = Field(default_factory=LoggingEnhanceConfig, description="Request trace correlation logging settings.")


def _legacy_config_candidates() -> tuple[Path, ...]:
    """Return source-tree config.yaml locations for monorepo compatibility."""
    backend_dir = Path(__file__).resolve().parents[4]
    repo_root = backend_dir.parent
    return (backend_dir / "config.yaml", repo_root / "config.yaml")


def logging_level_from_config(name: str | None) -> int:
    """Map ``config.yaml`` ``log_level`` string to a :mod:`logging` level constant."""
    mapping = logging.getLevelNamesMapping()
    return mapping.get((name or "info").strip().upper(), logging.INFO)


#: The single environment switch that turns diagnostics up. ``config.yaml``
#: ``log_level`` is a restart-required field served from
#: :data:`alpha.config.reload_boundary.STARTUP_ONLY_FIELDS`, and before this
#: variable existed there was no way to reach DEBUG on a running deployment at
#: all -- the process had to be restarted with an edited config file. Read it in
#: :func:`apply_logging_level` so every caller (the Gateway lifespan, the
#: embedded client, ``debug.py``) honours the same override.
LOG_LEVEL_ENV: Final[str] = "AGENT_WORKSPACE_LOG_LEVEL"


def logging_level_name_from_env(environ: Mapping[str, str] | None = None) -> str | None:
    """Return the ``AGENT_WORKSPACE_LOG_LEVEL`` override, or ``None`` when unset.

    An unrecognised value is reported and discarded rather than coerced, so a
    typo cannot silently change the effective level. Returning ``None`` leaves
    the ``config.yaml`` value in charge.
    """
    source = os.environ if environ is None else environ
    raw = source.get(LOG_LEVEL_ENV)
    if raw is None or not raw.strip():
        return None
    candidate = raw.strip()
    if candidate.upper() not in logging.getLevelNamesMapping():
        logger.warning(
            "%s=%r is not a logging level; ignoring it and using the configured log_level. Expected one of %s.",
            LOG_LEVEL_ENV,
            raw,
            sorted(name for name in logging.getLevelNamesMapping() if name.isupper()),
        )
        return None
    return candidate


def apply_logging_level(name: str | None) -> None:
    """Resolve *name* to a logging level and apply it to the ``alpha``/``app`` logger hierarchies.

    ``AGENT_WORKSPACE_LOG_LEVEL`` takes precedence over *name* when it holds a
    valid level, so one environment variable is enough to move a deployment to
    ``debug`` without editing a restart-required config field.

    Only the ``alpha`` and ``app`` logger levels are changed so that
    third-party library verbosity (e.g. uvicorn, sqlalchemy) is not
    affected. Root handler levels are lowered (never raised) so that
    messages from the configured loggers can propagate through without
    being filtered, while preserving handler thresholds that may be
    intentionally restrictive for third-party log output.
    """
    effective = logging_level_name_from_env() or name
    level = logging_level_from_config(effective)
    for logger_name in ("alpha", "app"):
        logging.getLogger(logger_name).setLevel(level)
    for handler in logging.root.handlers:
        if level < handler.level:
            handler.setLevel(level)


class AppConfig(BaseModel):
    """Config for the Alpha application"""

    log_level: str = Field(
        default="info",
        description=format_field_description(
            "log_level",
            field_doc="Logging level for alpha and app modules (debug/info/warning/error); third-party libraries are not affected.",
        ),
    )
    logging: LoggingConfig = Field(
        default_factory=LoggingConfig,
        description=format_field_description(
            "logging",
            field_doc="Structured logging settings: whether request trace ids appear in log records, and in which format.",
        ),
    )
    token_usage: TokenUsageConfig = Field(default_factory=TokenUsageConfig, description="Token usage tracking configuration")
    token_budget: TokenBudgetConfig = Field(default_factory=TokenBudgetConfig, description="Token Budget tracking and limits configuration.")
    plugins: list[ExtensionSpec] = Field(
        default_factory=list,
        description=format_field_description(
            "plugins",
            field_doc=(
                "Extension packages to load at startup, in order. Each entry names an install "
                "entry point as 'module.path:install' and carries its own private config block. "
                "Distinct from the `extensions` field above, which configures MCP servers, skills "
                "and config-declared middlewares and is backed by the HTTP-writable "
                "extensions_config.json."
            ),
        ),
    )
    max_recursion_limit: int = Field(
        default=1000,
        ge=1,
        description="Hard server-side ceiling for a client-supplied run recursion_limit. Client values above this are clamped; prevents runaway LangGraph super-steps (LLM cost / DoS).",
    )
    models: list[ModelConfig] = Field(default_factory=list, description="Available models")
    providers: dict[str, ProviderConfig] = Field(
        default_factory=dict,
        description="Named provider profiles: shared connection defaults referenced by models[].provider",
    )
    sandbox: SandboxConfig = Field(
        description=format_field_description(
            "sandbox",
            field_doc="Sandbox provider configuration (local filesystem or Docker-based aio sandbox).",
        ),
    )
    tools: list[ToolConfig] = Field(default_factory=list, description="Available tools")
    tool_groups: list[ToolGroupConfig] = Field(default_factory=list, description="Available tool groups")
    agent_presets: dict[str, AgentPresetConfig] = Field(default_factory=dict, description="Named per-session agent presets (standard/minimal/...). Empty selects built-in defaults; requested via configurable/context key 'agent_preset'.")
    skills: SkillsConfig = Field(default_factory=SkillsConfig, description="Skills configuration")
    skill_scan: SkillScanConfig = Field(default_factory=SkillScanConfig, description="Native deterministic skill safety scanning configuration")
    skill_evolution: SkillEvolutionConfig = Field(default_factory=SkillEvolutionConfig, description="Agent-managed skill evolution configuration")
    evolution_evidence: EvolutionEvidenceConfig = Field(
        default_factory=EvolutionEvidenceConfig,
        description=(
            "Self-evolution evidence gate: the bar a proposed self-change must clear before it "
            "becomes the new default. Requires a clean evaluator-integrity report, all required "
            "gates green, a primary-metric win beyond a declared noise floor, no regression, a "
            "rollback path, and an in-policy blast radius. Off by default."
        ),
    )
    self_tuning: SelfTuningConfig = Field(
        default_factory=SelfTuningConfig,
        description=(
            "Self-configuration change protocol: a proposal must pass validation, a scoped canary, "
            "an atomic apply, a health check, and automatic rollback. Protected paths (auth, "
            "authorization, sandbox, approvals, audit-enabling flags, enforcement ceilings) are "
            "refused even with an operator token. Off by default."
        ),
    )
    extensions: ExtensionsConfig = Field(default_factory=ExtensionsConfig, description="Extensions configuration (MCP servers and skills state)")
    tool_output: ToolOutputConfig = Field(default_factory=ToolOutputConfig, description="Tool output budget protection configuration")
    tool_search: ToolSearchConfig = Field(default_factory=ToolSearchConfig, description="Tool search / deferred loading configuration")
    title: TitleConfig = Field(default_factory=TitleConfig, description="Automatic title generation configuration")
    summarization: SummarizationConfig = Field(default_factory=SummarizationConfig, description="Conversation summarization configuration")
    task_continuity: TaskContinuityConfig = Field(default_factory=TaskContinuityConfig, description="Thread-local notes and compacted-source recall")
    memory: MemoryConfig = Field(default_factory=MemoryConfig, description="Memory subsystem configuration")
    agents_api: AgentsApiConfig = Field(default_factory=AgentsApiConfig, description="Custom-agent management API configuration")
    agent_presets: dict[str, AgentPresetConfig] = Field(default_factory=dict, description="Named per-session agent presets for DeepSeek-Harness style execution modes.")
    acp_agents: dict[str, ACPAgentConfig] = Field(default_factory=dict, description="ACP-compatible agent configuration")
    subagents: SubagentsAppConfig = Field(default_factory=SubagentsAppConfig, description="Subagent runtime configuration")
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig, description="Guardrail middleware configuration")
    hooks: HooksConfig = Field(default_factory=HooksConfig, description="Claude/Codex-style shell-hook bridge configuration (disabled by default)")
    authorization: AuthorizationConfig = Field(default_factory=AuthorizationConfig, description="Fine-grained resource authorization configuration (RBAC and beyond)")
    input_polish: InputPolishConfig = Field(default_factory=InputPolishConfig, description="Pre-send input polishing configuration.")
    voice: VoiceConfig = Field(default_factory=VoiceConfig, description="Voice & multimodal (mic/speaker/wake-word, TTS/STT/OCR/image) configuration.")
    suggestions: SuggestionsConfig = Field(default_factory=SuggestionsConfig, description="Follow-up suggestions configuration.")
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig, description="LLM circuit breaker configuration")
    llm_call: LlmCallConfig = Field(default_factory=LlmCallConfig, description="LLM call execution configuration (concurrency / rate shaping)")
    channel_connections: ChannelConnectionsConfig = Field(
        default_factory=ChannelConnectionsConfig,
        description=format_field_description(
            "channel_connections",
            field_doc="User-facing IM channel connection configuration.",
        ),
    )
    loop_detection: LoopDetectionConfig = Field(default_factory=LoopDetectionConfig, description="Loop detection middleware configuration")
    tool_progress: ToolProgressConfig = Field(default_factory=ToolProgressConfig, description="Tool progress state machine middleware configuration")
    verification: VerificationConfig = Field(default_factory=VerificationConfig, description="Subagent result verification (receipts, checklist, judge)")
    system_one: SystemOneConfig = Field(default_factory=SystemOneConfig, description="System One (hosted Jev or local Laya) fast structured-decision engine used as the default classifier/router/scorer with LLM fallback")
    read_before_write: ReadBeforeWriteConfig = Field(default_factory=ReadBeforeWriteConfig, description="Read-before-write file gate middleware configuration")
    review_guard: ReviewGuardConfig = Field(default_factory=ReviewGuardConfig, description="Review guard middleware configuration (comment density, role-scoped writes)")
    safety_finish_reason: SafetyFinishReasonConfig = Field(default_factory=SafetyFinishReasonConfig, description="Provider safety-filter finish_reason interception middleware configuration")
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig, description="Self-running subsystems: event bus, observe-only middlewares and background loops (AutonomySupervisor).")
    capabilities: CapabilitiesConfig = Field(default_factory=CapabilitiesConfig, description="Opt-in capability subsystems (see alpha.capabilities.catalog); all off unless enabled here.")
    auth: AuthAppConfig = Field(default_factory=AuthAppConfig, description="Authentication configuration (local + OIDC SSO)")
    model_config = ConfigDict(extra="allow")
    database: DatabaseConfig = Field(
        default_factory=DatabaseConfig,
        description=format_field_description(
            "database",
            field_doc="Unified database backend for run/feedback metadata (memory, sqlite, or postgres).",
        ),
    )
    run_events: RunEventsConfig = Field(
        default_factory=RunEventsConfig,
        description=format_field_description(
            "run_events",
            field_doc="Run-event store backend (memory for dev, db for production queries, jsonl for lightweight single-node persistence).",
        ),
    )
    agent_storage: AgentStorageConfig = Field(
        default_factory=AgentStorageConfig,
        description=format_field_description(
            "agent_storage",
            field_doc="Custom-agent and managed-subagent definition storage backend ('file' for on-disk layouts, 'db' to share definitions across nodes via SQL).",
        ),
    )
    scheduler: SchedulerConfig = Field(
        default_factory=SchedulerConfig,
        description=format_field_description(
            "scheduler",
            field_doc="Scheduled task runtime configuration (background poller for one-time, cron, and interval agent runs).",
        ),
    )
    mcp_tasks: McpTasksConfig = Field(
        default_factory=McpTasksConfig,
        description=format_field_description(
            "mcp_tasks",
            field_doc="Long-running MCP task persistence and background polling runtime.",
        ),
    )
    subagent_runtime: SubagentRuntimeConfig = Field(
        default_factory=SubagentRuntimeConfig,
        description=format_field_description(
            "subagent_runtime",
            field_doc="Process-local admission and execution capacity shared by ordinary and batch subagents.",
        ),
    )
    subagent_batches: SubagentBatchesConfig = Field(
        default_factory=SubagentBatchesConfig,
        description=format_field_description(
            "subagent_batches",
            field_doc="Durable native-subagent batch scheduling, lease, and recovery configuration.",
        ),
    )
    checkpointer: CheckpointerConfig | None = Field(
        default=None,
        description=format_field_description(
            "checkpointer",
            field_doc="LangGraph state-persistence checkpointer configuration.",
        ),
    )
    stream_bridge: StreamBridgeConfig | None = Field(
        default=None,
        description=format_field_description(
            "stream_bridge",
            field_doc="Stream bridge connecting agent workers to SSE endpoints.",
        ),
    )
    run_ownership: RunOwnershipConfig = Field(
        default_factory=RunOwnershipConfig,
        description=format_field_description(
            "run_ownership",
            field_doc="Run ownership, lease, and safe checkpoint auto-resume configuration for single- and multi-worker deployments.",
        ),
    )
    dedupe_storage: DedupeStorageConfig = Field(
        default_factory=DedupeStorageConfig,
        description=format_field_description(
            "dedupe_storage",
            field_doc="Inbound webhook dedupe storage backend (memory / postgres / auto) for cross-pod redelivery dedup. See issue #4120.",
        ),
    )

    # Name -> config lookup tables, (re)built after validation by
    # ``_build_name_indexes``. They make ``get_model_config`` / ``get_tool_config``
    # / ``get_tool_group_config`` O(1) instead of an O(n) ``next(...)`` scan per
    # call. Private attrs are excluded from serialization.
    _models_by_name: dict[str, ModelConfig] = PrivateAttr(default_factory=dict)
    _tools_by_name: dict[str, ToolConfig] = PrivateAttr(default_factory=dict)
    _tool_groups_by_name: dict[str, ToolGroupConfig] = PrivateAttr(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _drop_null_config_sections(cls, data: Any) -> Any:
        """Treat a present-but-null config section as absent so its default applies.

        Commenting out every entry under a top-level YAML key — e.g. ``models:``
        (a list) or ``memory:`` (an object), with only comments beneath it as
        shipped throughout ``config.example.yaml`` — makes PyYAML parse the value
        as ``None``. Without this, the documented ``cp config.example.yaml
        config.yaml`` first-run flow crashes with an opaque ``Input should be a
        valid list`` / ``valid dictionary`` pydantic error for that section.

        Dropping the ``None`` lets each field fall back to its default: list
        sections become ``[]`` via ``default_factory=list`` and object sections
        get their default config. This generalizes the earlier list-only
        handling to every section that defines a default. The ``database``
        section is independent and still owned by ``_apply_database_defaults``
        (in ``from_file``), which applies concrete defaults beyond null-coercion.
        Required sections without a default (``sandbox``) intentionally still
        error when null — there is nothing to fall back to.
        """
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if value is not None}
        return data

    @classmethod
    def resolve_config_path(cls, config_path: str | None = None) -> Path:
        """Resolve the config file path.

        Priority:
        1. If provided `config_path` argument, use it.
        2. If provided `AGENT_WORKSPACE_CONFIG_PATH` environment variable, use it.
        3. Otherwise, search the caller project root.
        4. Finally, search legacy backend/repository-root defaults for monorepo compatibility.
        """
        if config_path:
            path = Path(config_path)
            if not Path.exists(path):
                raise FileNotFoundError(f"Config file specified by param `config_path` not found at {path}")
            return path
        elif os.getenv("AGENT_WORKSPACE_CONFIG_PATH"):
            path = Path(os.getenv("AGENT_WORKSPACE_CONFIG_PATH"))
            if not Path.exists(path):
                raise FileNotFoundError(f"Config file specified by environment variable `AGENT_WORKSPACE_CONFIG_PATH` not found at {path}")
            return path
        else:
            project_config = existing_project_file(("config.yaml",))
            if project_config is not None:
                return project_config

            for path in _legacy_config_candidates():
                if path.exists():
                    return path
            raise FileNotFoundError("`config.yaml` file not found in the project root or legacy backend/repository root locations")

    @classmethod
    def from_file(cls, config_path: str | None = None) -> Self:
        """Load config from YAML file.

        See `resolve_config_path` for more details.

        Args:
            config_path: Path to the config file.

        Returns:
            AppConfig: The loaded config.
        """
        resolved_path = cls.resolve_config_path(config_path)
        with open(resolved_path, encoding="utf-8") as f:
            config_data = _yaml_safe_load(f) or {}

        # Check config version before processing
        cls._check_config_version(config_data, resolved_path)

        config_data = cls.resolve_env_variables(config_data)
        cls._apply_database_defaults(config_data)

        # Load circuit_breaker config if present
        if "circuit_breaker" in config_data:
            config_data["circuit_breaker"] = config_data["circuit_breaker"]

        # Load extensions config separately (it's in a different file), while
        # preserving any config.yaml-backed extension fields. config.yaml wins
        # when it explicitly declares a field because those values are part of
        # the main AppConfig hot-reload contract.
        yaml_extensions = config_data.get("extensions")
        extensions_config = ExtensionsConfig.from_file()
        extensions_data = extensions_config.model_dump(by_alias=True)
        if isinstance(yaml_extensions, Mapping):
            yaml_extensions_config = ExtensionsConfig.model_validate(yaml_extensions)
            extensions_data.update(yaml_extensions_config.model_dump(by_alias=True, exclude_unset=True))
        config_data["extensions"] = extensions_data

        result = cls.model_validate(config_data)
        if not result.models:
            logger.warning(
                "No models are configured in %s. Add at least one entry under `models:` (see the commented examples in config.example.yaml) or run `make setup`.",
                resolved_path,
            )
        acp_agents = cls._validate_acp_agents(config_data.get("acp_agents", {}))
        cls._apply_singleton_configs(result, acp_agents)
        return result

    @classmethod
    def _validate_acp_agents(
        cls,
        config_data: Mapping[str, Mapping[str, object]] | None,
    ) -> dict[str, ACPAgentConfig]:
        if config_data is None:
            config_data = {}
        return {name: ACPAgentConfig(**cfg) for name, cfg in config_data.items()}

    @classmethod
    def _apply_singleton_configs(cls, config: Self, acp_agents: dict[str, ACPAgentConfig]) -> None:
        from alpha.config.checkpointer_config import get_checkpointer_config

        previous_checkpointer_config = get_checkpointer_config()

        load_title_config_from_dict(config.title.model_dump())
        load_summarization_config_from_dict(config.summarization.model_dump())
        load_memory_config_from_dict(config.memory.model_dump())
        load_agents_api_config_from_dict(config.agents_api.model_dump())
        load_subagents_config_from_dict(config.subagents.model_dump())
        load_tool_search_config_from_dict(config.tool_search.model_dump())
        load_guardrails_config_from_dict(config.guardrails.model_dump())
        load_authorization_config_from_dict(config.authorization.model_dump())
        load_checkpointer_config_from_dict(config.checkpointer.model_dump() if config.checkpointer is not None else None)
        load_stream_bridge_config_from_dict(config.stream_bridge.model_dump() if config.stream_bridge is not None else None)
        load_acp_config_from_dict({name: agent.model_dump() for name, agent in acp_agents.items()})

        if previous_checkpointer_config != config.checkpointer:
            # These runtime singletons derive their backend from checkpointer config.
            # Keep imports local to avoid cycles: both providers import get_app_config.
            #
            # The unified ``database`` section is intentionally NOT handled here.
            # ``database`` is a restart-required field (reload_boundary.STARTUP_ONLY_FIELDS):
            # ``init_engine_from_config()`` builds the ORM engine once at startup and
            # never rebuilds it on a config.yaml edit. Resetting only the sync
            # checkpointer/store singletons on a live ``database``/``postgres_schema``
            # change would half-migrate the deployment -- new checkpoint/store tables
            # would land in the new schema while ORM rows keep landing in the old one,
            # with no error surfaced. Requiring the documented restart keeps the
            # deployment self-consistent.
            from alpha.runtime.checkpointer import reset_checkpointer
            from alpha.runtime.store import reset_store

            reset_checkpointer()
            reset_store()

    @classmethod
    def _apply_database_defaults(cls, config_data: dict[str, Any]) -> None:
        """Apply config.yaml defaults for persistence when the section is absent."""
        database_config = config_data.get("database")
        if database_config is None:
            database_config = {}
            config_data["database"] = database_config
        if not isinstance(database_config, dict):
            return
        for key, value in CONFIG_FILE_DATABASE_DEFAULTS.items():
            database_config.setdefault(key, value)

    @classmethod
    def _check_config_version(cls, config_data: dict, config_path: Path) -> None:
        """Check if the user's config.yaml is outdated compared to config.example.yaml.

        Emits a warning if the user's config_version is lower than the example's.
        Missing config_version is treated as version 0 (pre-versioning).
        """
        try:
            user_version = int(config_data.get("config_version", 0))
        except (TypeError, ValueError):
            user_version = 0

        # Find config.example.yaml by searching config.yaml's directory and its parents
        example_path = None
        search_dir = config_path.parent
        for _ in range(5):  # search up to 5 levels
            candidate = search_dir / "config.example.yaml"
            if candidate.exists():
                example_path = candidate
                break
            parent = search_dir.parent
            if parent == search_dir:
                break
            search_dir = parent
        if example_path is None:
            return

        try:
            with open(example_path, encoding="utf-8") as f:
                example_data = _yaml_safe_load(f)
            raw = example_data.get("config_version", 0) if example_data else 0
            try:
                example_version = int(raw)
            except (TypeError, ValueError):
                example_version = 0
        except Exception:
            return

        if user_version < example_version:
            logger.warning(
                "Your config.yaml (version %d) is outdated — the latest version is %d. Run `make config-upgrade` to merge new fields into your config.",
                user_version,
                example_version,
            )

    @classmethod
    def resolve_env_variables(cls, config: Any) -> Any:
        """Recursively resolve environment variables in the config.

        Environment variables are resolved using the `os.getenv` function. Example: $OPENAI_API_KEY

        Args:
            config: The config to resolve environment variables in.

        Returns:
            The config with environment variables resolved.
        """
        if isinstance(config, str):
            if config.startswith("$"):
                env_name = config[1:]
                env_value = os.getenv(env_name)
                if env_value is None:
                    # An unset optional secret must not abort startup. The
                    # shipped config.example.yaml references dozens of provider
                    # keys (OPENROUTER_API_KEY, BRAVE_SEARCH_API_KEY, ...), and
                    # both install.ps1 and start.ps1 copy it verbatim to
                    # config.yaml on a fresh install. Raising here crashed the
                    # Gateway before it could serve /health or the UI, so the
                    # app was dead on arrival for every new user. Degrade to an
                    # empty value and let the integration that actually needs
                    # the key fail loudly when it is used.
                    logger.warning(
                        "Environment variable %s is not set; config value %s resolved to an empty string. Set it in .env or the environment to enable this integration.",
                        env_name,
                        config,
                    )
                    return ""
                return env_value
            return config
        elif isinstance(config, dict):
            return {k: cls.resolve_env_variables(v) for k, v in config.items()}
        elif isinstance(config, list):
            return [cls.resolve_env_variables(item) for item in config]
        return config

    @model_validator(mode="after")
    def _build_name_indexes(self) -> "AppConfig":
        """Build name -> config lookup tables for O(1) ``get_*_config``.

        ``get_tool_config`` runs 2-3x per community-tool invocation (e.g.
        web_search) and ``get_model_config`` several times per agent build, so
        the previous O(n) ``next(...)`` scans sat on hot paths. Rebuilt here so a
        config reload (which constructs a fresh ``AppConfig``) refreshes them.
        ``setdefault`` keeps the first entry on duplicate names, preserving the
        prior ``next(...)`` first-match semantics.
        """
        models_by_name: dict[str, ModelConfig] = {}
        for model in self.models:
            models_by_name.setdefault(model.name, model)
        tools_by_name: dict[str, ToolConfig] = {}
        for tool in self.tools:
            tools_by_name.setdefault(tool.name, tool)
        tool_groups_by_name: dict[str, ToolGroupConfig] = {}
        for group in self.tool_groups:
            tool_groups_by_name.setdefault(group.name, group)
        self._models_by_name = models_by_name
        self._tools_by_name = tools_by_name
        self._tool_groups_by_name = tool_groups_by_name
        return self

    def get_model_config(self, name: str) -> ModelConfig | None:
        """Get the model config by name.

        Args:
            name: The name of the model to get the config for.

        Returns:
            The model config if found, otherwise None.
        """
        cfg = self._models_by_name.get(name)
        if cfg is not None:
            return cfg
        if name and (name == "alpha-free" or name.startswith("free:") or name.startswith("alpha-free:")):
            if name == "alpha-free":
                target_prov = None
                target_m = "auto"
            else:
                parts = name.split(":", 2)
                target_prov = parts[1] if len(parts) >= 2 else None
                target_m = parts[2] if len(parts) >= 3 else "auto"
            synth = ModelConfig(
                name=name,
                display_name="Alpha Free (Auto-Router)" if name == "alpha-free" else name,
                use="alpha.models.free_router:ChatFreeLLM",
                model=target_m,
                target_provider=target_prov,
            )
            self._models_by_name[name] = synth
            return synth

        # Dynamic synthesis for known provider models and user-configured custom models
        try:
            from alpha.models.provider_manager import PROVIDER_SPECS, load_credentials_file

            for spec in PROVIDER_SPECS:
                for dm in spec.default_models:
                    if dm.id == name:
                        creds = load_credentials_file()
                        persisted = creds.get("providers", {}).get(spec.id, {})
                        api_key = (spec.key_env and os.getenv(spec.key_env)) or persisted.get("api_key") or ""
                        base_url = persisted.get("base_url") or spec.default_base_url
                        kwargs: dict[str, Any] = {}
                        if api_key:
                            kwargs["api_key"] = api_key
                        if base_url:
                            kwargs["base_url"] = base_url
                        synth = ModelConfig(
                            name=name,
                            model=dm.model_id,
                            display_name=dm.name,
                            description=dm.description,
                            use=spec.default_use,
                            supports_thinking=dm.supports_thinking,
                            **kwargs,
                        )
                        self._models_by_name[name] = synth
                        return synth

            creds = load_credentials_file()
            for cm in creds.get("custom_models", []):
                if cm.get("id") == name or cm.get("name") == name:
                    synth = ModelConfig(
                        name=name,
                        model=cm.get("model", name),
                        display_name=cm.get("name", name),
                        description=f"Custom model ({cm.get('provider', 'custom')})",
                        use="langchain_openai:ChatOpenAI",
                        api_key=cm.get("api_key") or os.getenv("CUSTOM_LLM_API_KEY", ""),
                        base_url=cm.get("base_url", "http://127.0.0.1:11434/v1"),
                        supports_thinking=cm.get("supports_thinking", False),
                    )
                    self._models_by_name[name] = synth
                    return synth
        except Exception as exc:
            logger.debug("Failed dynamic provider lookup for model '%s': %s", name, exc)

        return None

    def get_provider_config(self, name: str) -> ProviderConfig | None:
        """Get a named provider profile by name.

        Args:
            name: The provider name referenced by models[].provider.

        Returns:
            The provider profile if found, otherwise None.
        """
        return self.providers.get(name)

    def get_tool_config(self, name: str) -> ToolConfig | None:
        """Get the tool config by name.

        Args:
            name: The name of the tool to get the config for.

        Returns:
            The tool config if found, otherwise None.
        """
        return self._tools_by_name.get(name)

    def get_tool_group_config(self, name: str) -> ToolGroupConfig | None:
        """Get the tool group config by name.

        Args:
            name: The name of the tool group to get the config for.

        Returns:
            The tool group config if found, otherwise None.
        """
        return self._tool_groups_by_name.get(name)


# Compatibility singleton layer for code paths that have not yet been
# migrated to explicit ``AppConfig`` threading. New composition roots should
# prefer constructing ``AppConfig`` once and passing it down directly.
_app_config: AppConfig | None = None
_app_config_path: Path | None = None
_app_config_mtime: float | None = None
_app_config_signature: _ConfigSignature | None = None
_app_config_is_custom = False
_current_app_config: ContextVar[AppConfig | None] = ContextVar("agent_workspace_current_app_config", default=None)
_current_app_config_stack: ContextVar[tuple[AppConfig | None, ...]] = ContextVar("agent_workspace_current_app_config_stack", default=())


def _get_config_mtime(config_path: Path) -> float | None:
    """Get the modification time of a config file if it exists."""
    try:
        return config_path.stat().st_mtime
    except OSError:
        return None


# Guards the cold-load path so concurrent first callers share one parse
# instead of each parsing config.yaml (and config.example.yaml) in parallel.
# Without it, N threads starting together pay the full load N times — measured
# at ~6.6 s per cold load before the C-loader fix, and a thundering herd at
# startup is exactly when several threads hit this at once. Reentrant because
# ``AppConfig.from_file`` -> ``_apply_singleton_configs`` reaches other
# providers that can call back into ``get_app_config()`` on the same thread.
_app_config_lock = threading.RLock()


def _load_and_cache_app_config(config_path: str | None = None) -> AppConfig:
    """Load config from disk and refresh cache metadata."""
    global _app_config, _app_config_path, _app_config_mtime, _app_config_signature, _app_config_is_custom

    resolved_path = AppConfig.resolve_config_path(config_path)
    _app_config = AppConfig.from_file(str(resolved_path))
    _app_config_path = resolved_path
    _app_config_mtime = _get_config_mtime(resolved_path)
    _app_config_signature = _get_config_signature(resolved_path)
    _app_config_is_custom = False
    return _app_config


def get_app_config() -> AppConfig:
    """Get the Alpha config instance.

    Returns a cached singleton instance and automatically reloads it when the
    underlying config file path or content signature changes. Use
    `reload_app_config()` to force a reload, or `reset_app_config()` to clear
    the cache.
    """
    global _app_config, _app_config_path, _app_config_mtime, _app_config_signature

    runtime_override = _current_app_config.get()
    if runtime_override is not None:
        return runtime_override

    if _app_config is not None and _app_config_is_custom:
        return _app_config

    resolved_path = AppConfig.resolve_config_path()
    current_signature = _get_config_signature(resolved_path)

    # Fast path: an unchanged, already-loaded config needs no lock at all.
    if _app_config is not None and _app_config_path == resolved_path and _app_config_signature == current_signature:
        return _app_config

    with _app_config_lock:
        # Re-read every input under the lock: another thread may have finished
        # the load while we waited, and reloading again would re-run
        # ``_apply_singleton_configs`` — which resets the checkpointer and
        # store singletons — for no reason.
        runtime_override = _current_app_config.get()
        if runtime_override is not None:
            return runtime_override
        if _app_config is not None and _app_config_is_custom:
            return _app_config

        current_mtime = _get_config_mtime(resolved_path)
        current_signature = _get_config_signature(resolved_path)

        should_reload = _app_config is None or _app_config_path != resolved_path or _app_config_signature != current_signature
        if should_reload:
            if _app_config_path == resolved_path and _app_config_mtime is not None and current_mtime is not None and _app_config_mtime != current_mtime:
                logger.info(
                    "Config file has been modified (mtime: %s -> %s), reloading AppConfig",
                    _app_config_mtime,
                    current_mtime,
                )
            elif _app_config_path == resolved_path and _app_config_signature != current_signature:
                logger.info("Config file content signature changed, reloading AppConfig")
            _load_and_cache_app_config(str(resolved_path))
    return _app_config


def reload_app_config(config_path: str | None = None) -> AppConfig:
    """Reload the config from file and update the cached instance.

    This is useful when the config file has been modified and you want
    to pick up the changes without restarting the application.

    Args:
        config_path: Optional path to config file. If not provided,
                     uses the default resolution strategy.

    Returns:
        The newly loaded AppConfig instance.
    """
    return _load_and_cache_app_config(config_path)


def reset_app_config() -> None:
    """Reset the cached config instance.

    This clears the singleton cache, causing the next call to
    `get_app_config()` to reload from file. Useful for testing
    or when switching between different configurations.
    """
    global _app_config, _app_config_path, _app_config_mtime, _app_config_signature, _app_config_is_custom
    _app_config = None
    _app_config_path = None
    _app_config_mtime = None
    _app_config_signature = None
    _app_config_is_custom = False


def set_app_config(config: AppConfig) -> None:
    """Set a custom config instance.

    This allows injecting a custom or mock config for testing purposes.

    Args:
        config: The AppConfig instance to use.
    """
    global _app_config, _app_config_path, _app_config_mtime, _app_config_signature, _app_config_is_custom
    _app_config = config
    _app_config_path = None
    _app_config_mtime = None
    _app_config_signature = None
    _app_config_is_custom = True


def peek_current_app_config() -> AppConfig | None:
    """Return the runtime-scoped AppConfig override, if one is active."""
    return _current_app_config.get()


def push_current_app_config(config: AppConfig) -> None:
    """Push a runtime-scoped AppConfig override for the current execution context."""
    stack = _current_app_config_stack.get()
    _current_app_config_stack.set(stack + (_current_app_config.get(),))
    _current_app_config.set(config)


def pop_current_app_config() -> None:
    """Pop the latest runtime-scoped AppConfig override for the current execution context."""
    stack = _current_app_config_stack.get()
    if not stack:
        _current_app_config.set(None)
        return
    previous = stack[-1]
    _current_app_config_stack.set(stack[:-1])
    _current_app_config.set(previous)
