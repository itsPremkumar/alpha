"""Configuration for the opt-in codebase structure-memory subsystem.

The subsystem is deliberately independent of the shared application config so
it can be injected in tests and embedded hosts without creating an import
cycle.  ``enabled`` defaults to ``False``; constructing an indexer or recall
engine with the default therefore performs no listing, reading, or storage
operation.  Every field is consumed by a concrete module in this package.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

_DEFAULT_EXTENSIONS = (
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".php",
    ".py",
    ".pyi",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
)


class CodebaseConfig(BaseModel):
    """Bounded, default-off settings for structure indexing and recall."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = Field(default=False, description="Master index/recall gate; default is off.")
    max_files: int = Field(default=2000, ge=1, le=1_000_000, validation_alias=AliasChoices("max_files", "file_count_limit"), description="Maximum eligible files considered per refresh.")
    max_file_bytes: int = Field(default=1_000_000, ge=1, le=100_000_000, validation_alias=AliasChoices("max_file_bytes", "max_file_size"), description="Maximum bytes read from one file.")
    max_symbols_per_file: int = Field(default=500, ge=1, le=100_000, validation_alias=AliasChoices("max_symbols_per_file", "max_symbols"), description="Maximum symbols retained per file.")
    max_snapshot_history: int = Field(default=10, ge=1, le=1000, validation_alias=AliasChoices("max_snapshot_history", "snapshot_history_limit", "history_limit"), description="Bounded per-repository snapshot history.")
    symbols_char_budget: int = Field(
        default=2400,
        ge=0,
        le=1_000_000,
        validation_alias=AliasChoices("symbols_char_budget", "symbols_budget", "symbols_recall_chars", "recall_symbols_chars", "symbols_block_chars"),
    )
    module_summary_char_budget: int = Field(
        default=2400,
        ge=0,
        le=1_000_000,
        validation_alias=AliasChoices("module_summary_char_budget", "module_summary_budget", "module_summary_recall_chars", "recall_module_summary_chars", "module_summary_block_chars"),
    )
    impact_char_budget: int = Field(
        default=1800,
        ge=0,
        le=1_000_000,
        validation_alias=AliasChoices("impact_char_budget", "impact_budget", "impact_recall_chars", "recall_impact_chars", "impact_block_chars"),
    )
    structure_char_budget: int = Field(
        default=3000,
        ge=0,
        le=1_000_000,
        validation_alias=AliasChoices("structure_char_budget", "structure_budget", "structure_recall_chars", "recall_structure_chars", "structure_block_chars"),
    )
    max_recall_items: int = Field(
        default=20,
        ge=1,
        le=1000,
        validation_alias=AliasChoices("max_recall_items", "recall_top_k", "max_recall_results"),
    )
    max_impact_depth: int = Field(
        default=4,
        ge=1,
        le=100,
        validation_alias=AliasChoices("max_impact_depth", "impact_depth_cap", "max_transitive_depth"),
    )
    index_extensions: tuple[str, ...] = Field(
        default=_DEFAULT_EXTENSIONS,
        validation_alias=AliasChoices("index_extensions", "extensions", "file_extensions"),
    )
    storage_path: str | None = Field(default=None, description="Root for per-repository JSON snapshots; None uses runtime home.")

    @model_validator(mode="before")
    @classmethod
    def _expand_recall_budget_groups(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        grouped = data.pop("recall_char_budgets", None) or data.pop("recall_budgets", None) or data.pop("char_budgets", None)
        if isinstance(grouped, dict):
            names = {
                "symbols": "symbols_char_budget",
                "module_summary": "module_summary_char_budget",
                "impact": "impact_char_budget",
                "structure": "structure_char_budget",
            }
            for key, field_name in names.items():
                if key in grouped:
                    data.setdefault(field_name, grouped[key])
        return data

    @field_validator("index_extensions", mode="before")
    @classmethod
    def _normalize_extensions(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            value = (value,)
        normalized: set[str] = set()
        for item in value:
            text = str(item).strip().lower()
            if not text:
                continue
            normalized.add(text if text.startswith(".") else f".{text}")
        return tuple(sorted(normalized))

    @property
    def symbols_budget(self) -> int:
        return self.symbols_char_budget

    @property
    def module_summary_budget(self) -> int:
        return self.module_summary_char_budget

    @property
    def impact_budget(self) -> int:
        return self.impact_char_budget

    @property
    def structure_budget(self) -> int:
        return self.structure_char_budget

    @property
    def recall_budgets(self) -> dict[str, int]:
        return {
            "symbols": self.symbols_char_budget,
            "module_summary": self.module_summary_char_budget,
            "impact": self.impact_char_budget,
            "structure": self.structure_char_budget,
        }

    @property
    def recall_char_budgets(self) -> dict[str, int]:
        return self.recall_budgets

    @property
    def max_transitive_depth(self) -> int:
        return self.max_impact_depth


def get_codebase_config() -> CodebaseConfig:
    """Return an isolated default-off config.

    A host may pass an explicit ``CodebaseConfig`` to the indexer, store, or
    recall engine.  This function deliberately does not read or mutate the
    central Alpha config, which is outside this package's ownership boundary.
    """

    return CodebaseConfig()


__all__ = ["CodebaseConfig", "get_codebase_config"]
