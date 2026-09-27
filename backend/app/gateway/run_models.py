"""Shared request models for the LangGraph-compatible run boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError

from alpha.config.reasoning_effort import CANONICAL_EFFORTS, normalize_effort
from alpha.runtime.stream_modes import RunStreamMode, UnsupportedStreamModeError, normalize_stream_modes
from alpha.utils.thread_id import validate_thread_id


class AcceptanceCriterion(BaseModel):
    """A user-visible condition that must have independently collected evidence."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    description: str = Field(min_length=1, max_length=2_000)
    required_evidence_kinds: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("required_evidence_kinds")
    @classmethod
    def validate_evidence_kinds(cls, value: list[str]) -> list[str]:
        if any(not kind or len(kind) > 64 for kind in value):
            raise ValueError("evidence kinds must be non-empty strings up to 64 characters")
        if len(set(value)) != len(value):
            raise ValueError("evidence kinds must not contain duplicates")
        return value


def _canonicalize_effort_section(section: Any, path: str) -> Any:
    """Resolve ``reasoning_effort`` inside *section* to a canonical rung.

    The value rides into the checkpointed ``configurable`` block, into the
    assembly descriptor, into run metadata, and finally into the model
    constructor, so an alias like ``x-high`` must be resolved **once**, at the
    request boundary, rather than four times downstream where a partial fix
    would leave two layers disagreeing.

    A value that is present, non-empty, and names no rung is a client asking
    for a level that does not exist. Silently running at the model default
    would leave the picker showing a level the run never used, so it raises a
    422 that names the valid rungs.
    """
    if not isinstance(section, dict) or "reasoning_effort" not in section:
        return section
    requested = section["reasoning_effort"]
    if requested is None or requested == "":
        return section
    level = normalize_effort(requested)
    if level is not None:
        return {**section, "reasoning_effort": level}
    if str(requested).strip().lower() in _UNSET_EFFORT_SPELLINGS:
        # An explicit "let the provider decide" is a real request, distinct
        # from omitting the key: it must clear any value already in place.
        return {**section, "reasoning_effort": None}
    raise PydanticCustomError(
        "unsupported_reasoning_effort",
        "{path}.reasoning_effort '{value}' is not a reasoning effort level; use one of: {levels}",
        {"path": path, "value": str(requested)[:64], "levels": ", ".join(CANONICAL_EFFORTS)},
    )


#: Spellings that mean "send nothing and let the provider decide".
_UNSET_EFFORT_SPELLINGS: frozenset[str] = frozenset({"default", "auto", "provider", "inherit"})


class RunCreateRequest(BaseModel):
    """Validated run request used by both HTTP and internal launch paths."""

    model_config = ConfigDict(extra="forbid")

    assistant_id: str | None = Field(default=None, description="Agent / assistant to use")
    input: dict[str, Any] | None = Field(default=None, description="Graph input (e.g. {messages: [...]})")
    command: dict[str, Any] | None = Field(default=None, description="LangGraph Command")
    metadata: dict[str, Any] | None = Field(default=None, description="Run metadata")
    acceptance_criteria: list[AcceptanceCriterion] | None = Field(default=None, max_length=32, description="Optional criteria whose evidence determines verified completion")
    autonomous: bool = Field(default=False, description="Server-applied autonomous execution mode: planning, permitted delegation, and no clarification pauses")
    config: dict[str, Any] | None = Field(default=None, description="RunnableConfig overrides")
    context: dict[str, Any] | None = Field(default=None, description="Alpha context overrides (model_name, thinking_enabled, etc.)")
    webhook: None = Field(default=None, description="Compatibility placeholder; completion callbacks are not supported")
    checkpoint_id: str | None = Field(default=None, description="Resume from checkpoint")
    checkpoint: dict[str, Any] | None = Field(default=None, description="Full checkpoint object")
    interrupt_before: list[str] | Literal["*"] | None = Field(default=None, description="Nodes to interrupt before")
    interrupt_after: list[str] | Literal["*"] | None = Field(default=None, description="Nodes to interrupt after")
    stream_mode: list[RunStreamMode] | RunStreamMode | None = Field(default=None, description="Supported stream mode(s)")
    stream_subgraphs: bool = Field(default=False, description="Include subgraph events")
    stream_resumable: Literal[False] | None = Field(default=None, description="Compatibility placeholder; only the SDK's non-resumable default (null/false) is accepted")
    on_disconnect: Literal["cancel", "continue"] = Field(default="continue", description="Behaviour on SSE disconnect; explicit cancel remains available through the cancel endpoint")
    on_completion: None = Field(default=None, description="Compatibility placeholder; completion behavior is not supported")
    multitask_strategy: Literal["reject", "rollback", "interrupt"] = Field(default="reject", description="Concurrency strategy")
    after_seconds: None = Field(default=None, description="Compatibility placeholder; delayed execution is not supported")
    if_not_exists: Literal["create"] = Field(default="create", description="Compatibility default; missing threads are created")
    feedback_keys: None = Field(default=None, description="Compatibility placeholder; feedback key collection is not supported")

    @model_validator(mode="after")
    def validate_configurable_thread_id(self) -> RunCreateRequest:
        """Validate the stateless-run thread selector inside RunnableConfig."""
        if not isinstance(self.config, dict):
            return self
        configurable = self.config.get("configurable")
        if not isinstance(configurable, dict) or "thread_id" not in configurable:
            return self
        thread_id = configurable["thread_id"]
        if thread_id is not None:
            validate_thread_id(thread_id)
        return self

    @model_validator(mode="after")
    def validate_acceptance_criterion_ids(self) -> RunCreateRequest:
        identifiers = [criterion.id for criterion in self.acceptance_criteria or []]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("acceptance criteria must not contain duplicate ids")
        return self

    @field_validator("context", mode="before")
    @classmethod
    def normalize_reasoning_effort(cls, value: Any) -> Any:
        """Canonicalize ``context.reasoning_effort`` and reject a value that names no rung."""
        return _canonicalize_effort_section(value, "context")

    @field_validator("config", mode="before")
    @classmethod
    def normalize_reasoning_effort_in_config(cls, value: Any) -> Any:
        """Apply the same canonicalization to the free-form ``config`` carriers.

        The web client sends its run options under ``config.configurable`` while
        the LangGraph SDK channel sends them under ``body.context``, and
        ``merge_run_context_overrides`` then copies the latter into the former.
        Validating only one of the two would leave a hole exactly where the
        other kind of caller sends it.
        """
        if not isinstance(value, dict):
            return value
        updated = _canonicalize_effort_section(value.get("configurable"), "config.configurable")
        if updated is not value.get("configurable"):
            value = {**value, "configurable": updated}
        return _canonicalize_effort_section(value, "config")

    @field_validator(
        "webhook",
        "on_completion",
        "multitask_strategy",
        "after_seconds",
        "if_not_exists",
        "feedback_keys",
        mode="before",
    )
    @classmethod
    def reject_unsupported_run_options(cls, value: Any, info: ValidationInfo) -> Any:
        if info.field_name in {"multitask_strategy", "if_not_exists"} and not isinstance(value, str):
            return value

        supported_defaults = {
            "webhook": None,
            "on_completion": None,
            "multitask_strategy": {"reject", "rollback", "interrupt"},
            "after_seconds": None,
            "if_not_exists": "create",
            "feedback_keys": None,
        }
        supported = supported_defaults[info.field_name]
        if isinstance(supported, set):
            is_supported = isinstance(value, str) and value in supported
        else:
            is_supported = value == supported
        if not is_supported:
            raise PydanticCustomError(
                "unsupported_run_option",
                "Run option '{option}' is not supported by Alpha",
                {"option": info.field_name},
            )
        return value

    @field_validator("stream_resumable", mode="before")
    @classmethod
    def reject_resumable_streams(cls, value: Any) -> Any:
        # LangGraph SDK clients always send this field (its default is ``False``, which the
        # payload's ``None`` filter keeps). ``False`` asks for the non-resumable stream
        # Alpha already serves, so only an explicit ``True`` requests the unsupported feature.
        if value is None or value is False:
            return value
        raise PydanticCustomError(
            "unsupported_run_option",
            "Run option '{option}' is not supported by Alpha",
            {"option": "stream_resumable"},
        )

    @field_validator("stream_mode", mode="before")
    @classmethod
    def reject_unsupported_stream_modes(cls, value: Any) -> Any:
        if value is None:
            return value
        if not isinstance(value, str) and (not isinstance(value, list) or not all(isinstance(mode, str) for mode in value)):
            return value
        try:
            normalize_stream_modes(value)
        except UnsupportedStreamModeError as exc:
            modes = ", ".join(exc.modes)
            raise PydanticCustomError(
                "unsupported_stream_mode",
                "Unsupported stream mode(s): {modes}",
                {"modes": modes},
            ) from exc
        return value
