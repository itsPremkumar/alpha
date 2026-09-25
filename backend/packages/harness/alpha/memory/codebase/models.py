"""Typed records for durable codebase structure memory.

The records in this module intentionally contain *derived structure* rather than
source text.  The shape is inspired by the useful parts of SCIP/Sourcegraph
(symbol references and dependency edges) and CodeBERT-style bounded lexical
signals, but the implementation and serialization are original Alpha code.
The indexer never imports or executes the repository it describes.

All models are Pydantic models so a store can validate a JSON snapshot before
publishing it.  ``extra="forbid"`` is deliberate: a typo in a structural
record must not silently become an unreported signal.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

SymbolKind = Literal["module", "class", "function", "method", "constant", "type"]
DependencyKind = Literal["import", "call", "reference"]
ConfidenceLabel = Literal["high", "medium", "low"]
ParseStatus = Literal["parsed", "unparsed", "error"]


class SymbolRef(BaseModel):
    """A symbol and its source span, using a repository-relative path.

    ``line_start``/``line_end`` are inclusive, matching the line numbers shown
    by Python tracebacks and most code viewers.  The aliases ``start_line`` and
    ``end_line`` are accepted on input and exposed as properties for callers
    that use that spelling.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str = Field(validation_alias=AliasChoices("path", "file_path", "repo_path"))
    qualified_name: str = Field(validation_alias=AliasChoices("qualified_name", "name"))
    kind: SymbolKind
    line_start: int = Field(default=1, ge=1, validation_alias=AliasChoices("line_start", "start_line"))
    line_end: int = Field(default=1, ge=1, validation_alias=AliasChoices("line_end", "end_line"))

    @model_validator(mode="before")
    @classmethod
    def _coerce_line_span(cls, value: Any) -> Any:
        if isinstance(value, dict) and "line_span" in value:
            data = dict(value)
            span = data.pop("line_span")
            if isinstance(span, (tuple, list)) and len(span) == 2:
                data.setdefault("line_start", span[0])
                data.setdefault("line_end", span[1])
            return data
        return value

    @model_validator(mode="after")
    def _validate_span(self) -> SymbolRef:
        if self.line_end < self.line_start:
            raise ValueError("line_end must be greater than or equal to line_start")
        return self

    @property
    def repo_path(self) -> str:
        return self.path

    @property
    def name(self) -> str:
        """The final component of the qualified name."""

        return self.qualified_name.rsplit(".", 1)[-1]

    @property
    def start_line(self) -> int:
        return self.line_start

    @property
    def end_line(self) -> int:
        return self.line_end

    @property
    def line_span(self) -> tuple[int, int]:
        return self.line_start, self.line_end

    @property
    def span(self) -> tuple[int, int]:
        return self.line_span

    @property
    def line(self) -> int:
        return self.line_start


class ModuleRecord(BaseModel):
    """Bounded structure discovered for one source file.

    ``imports`` contains normalized import targets (module names when a target
    is outside the indexed tree).  ``dependents`` is filled from the graph and
    therefore contains indexed paths only.  Source text is never persisted.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str = Field(validation_alias=AliasChoices("path", "file_path", "repo_path"))
    summary: str = Field(default="", validation_alias=AliasChoices("summary", "docstring_first_line", "description"))
    symbols: list[SymbolRef] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    dependents: list[str] = Field(default_factory=list)
    language: str = Field(default="text", validation_alias=AliasChoices("language", "lang"))
    content_hash: str = Field(default="", validation_alias=AliasChoices("content_hash", "hash", "sha256"))
    indexed_at: float = Field(default=0.0, validation_alias=AliasChoices("indexed_at", "indexedAt"))
    size_bytes: int | None = Field(default=None, ge=0)
    mtime_ns: int | None = None
    parse_status: ParseStatus = "parsed"
    disclosures: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize_lists(self) -> ModuleRecord:
        self.symbols = sorted(
            self.symbols,
            key=lambda symbol: (symbol.path, symbol.line_start, symbol.qualified_name, symbol.kind),
        )
        self.imports = sorted({item for item in self.imports if item})
        self.dependents = sorted({item for item in self.dependents if item})
        self.disclosures = sorted({item for item in self.disclosures if item})
        return self

    @property
    def docstring_first_line(self) -> str:
        return self.summary

    @property
    def responsibility(self) -> str:
        return self.summary

    @property
    def hash(self) -> str:
        return self.content_hash

    @property
    def is_unparsed(self) -> bool:
        return self.parse_status == "unparsed"


class DependencyEdge(BaseModel):
    """A directed, evidence-bearing relationship between two paths/names."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_path: str = Field(validation_alias=AliasChoices("from_path", "source", "source_path", "from"))
    to_path: str = Field(validation_alias=AliasChoices("to_path", "target", "target_path", "to"))
    kind: DependencyKind
    weight: float = Field(default=1.0, ge=0.0)
    evidence: str = ""

    @model_validator(mode="after")
    def _validate_edge(self) -> DependencyEdge:
        if not self.from_path or not self.to_path:
            raise ValueError("dependency paths must be non-empty")
        return self

    @property
    def source(self) -> str:
        return self.from_path

    @property
    def target(self) -> str:
        return self.to_path

    @property
    def source_path(self) -> str:
        return self.from_path

    @property
    def target_path(self) -> str:
        return self.to_path


class Hotspot(BaseModel):
    """A disclosed heuristic hotspot score.

    A missing input is represented by ``None`` and named in
    ``missing_inputs``; it is never silently converted to zero.  ``inputs``
    records the raw/derived values and the formula identifier used.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str
    churn_score: float | None = None
    fan_in_score: float | None = None
    score: float | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    missing_inputs: list[str] = Field(default_factory=list)
    disclosures: list[str] = Field(default_factory=list)
    heuristic: bool = True

    @model_validator(mode="after")
    def _normalize_disclosures(self) -> Hotspot:
        self.missing_inputs = sorted({item for item in self.missing_inputs if item})
        self.disclosures = sorted({item for item in self.disclosures if item})
        return self

    @property
    def churn(self) -> float | None:
        return self.churn_score

    @property
    def fan_in(self) -> float | None:
        return self.fan_in_score

    @property
    def partial(self) -> bool:
        return bool(self.missing_inputs)

    @property
    def score_method(self) -> str:
        return "heuristic" if self.heuristic else "provided"

    @property
    def churn_input(self) -> object:
        return self.inputs.get("commit_timestamps", self.inputs.get("edit_count"))

    @property
    def fan_in_input(self) -> object:
        return self.inputs.get("fan_in")

    @property
    def signals(self) -> dict[str, Any]:
        return dict(self.inputs)


class CodebaseSnapshot(BaseModel):
    """A complete-or-explicitly-partial view of one repository.

    ``module_count`` and ``edge_count`` are persisted for cheap status reads;
    validators keep them synchronized with the record lists.  A partial index
    is never presented as complete: ``partial_index`` and ``disclosures`` carry
    the reasons (caps, unparsed files, parse/read errors, and so on).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    repo_id: str = Field(default="default", validation_alias=AliasChoices("repo_id", "repository_id"))
    generated_at: float = Field(default_factory=time.time, validation_alias=AliasChoices("generated_at", "generatedAt"))
    modules: list[ModuleRecord] = Field(default_factory=list)
    edges: list[DependencyEdge] = Field(default_factory=list)
    module_count: int = Field(default=0, ge=0)
    edge_count: int = Field(default=0, ge=0)
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    partial_index: bool = Field(default=False, validation_alias=AliasChoices("partial_index", "partial"))
    disclosures: list[str] = Field(default_factory=list)
    skipped_files: list[str] = Field(default_factory=list, validation_alias=AliasChoices("skipped_files", "skipped_paths"))
    skipped_reasons: dict[str, str] = Field(default_factory=dict)
    unparsed_files: list[str] = Field(default_factory=list)
    read_paths: list[str] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    removed_paths: list[str] = Field(default_factory=list)
    fingerprint_disclosures: list[str] = Field(default_factory=list)
    evicted_versions: list[int] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _normalize_snapshot(self) -> CodebaseSnapshot:
        self.modules = sorted(self.modules, key=lambda module: module.path)
        self.edges = sorted(self.edges, key=lambda edge: (edge.from_path, edge.to_path, edge.kind, edge.evidence))
        self.module_count = len(self.modules)
        self.edge_count = len(self.edges)
        self.skipped_files = sorted({item for item in self.skipped_files if item})
        self.unparsed_files = sorted({item for item in self.unparsed_files if item})
        self.read_paths = sorted({item for item in self.read_paths if item})
        self.changed_paths = sorted({item for item in self.changed_paths if item})
        self.removed_paths = sorted({item for item in self.removed_paths if item})
        self.fingerprint_disclosures = sorted({item for item in self.fingerprint_disclosures if item})
        self.evicted_versions = sorted({item for item in self.evicted_versions})
        self.disclosures = sorted({item for item in self.disclosures if item})
        self.skipped_reasons = {str(key): str(value) for key, value in sorted(self.skipped_reasons.items())}
        return self

    @property
    def module_map(self) -> dict[str, ModuleRecord]:
        return {module.path: module for module in self.modules}

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_files)

    @property
    def unparsed_count(self) -> int:
        return len(self.unparsed_files)

    @property
    def read_count(self) -> int:
        return len(self.read_paths)

    @property
    def changed_count(self) -> int:
        return len(self.changed_paths)

    @property
    def coverage_ratio(self) -> float:
        return self.coverage

    @property
    def partial(self) -> bool:
        return self.partial_index


class ChangeImpact(BaseModel):
    """Conservative reverse-dependency impact for one changed path.

    ``transitively_affected`` maps each affected indexed path to its minimum
    reverse-edge distance.  The changed path itself is not included.  A partial
    index is surfaced explicitly in ``partial_index``/``disclosures`` and lowers
    the confidence label rather than pretending the graph is complete.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str
    directly_affected: list[str] = Field(default_factory=list, validation_alias=AliasChoices("directly_affected", "directly_affected_paths"))
    transitively_affected: dict[str, int] = Field(default_factory=dict, validation_alias=AliasChoices("transitively_affected", "transitive_depths"))
    test_files: list[str] = Field(default_factory=list, validation_alias=AliasChoices("test_files", "test_file_paths"))
    confidence: ConfidenceLabel = Field(default="low", validation_alias=AliasChoices("confidence", "confidence_label"))
    computation: str = Field(default="", validation_alias=AliasChoices("computation", "how_computed"))
    partial_index: bool = Field(default=False, validation_alias=AliasChoices("partial_index", "partial"))
    disclosures: list[str] = Field(default_factory=list)
    depth_cap: int | None = Field(default=None, ge=1, validation_alias=AliasChoices("depth_cap", "max_depth"))

    @model_validator(mode="after")
    def _normalize_impact(self) -> ChangeImpact:
        self.directly_affected = sorted({item for item in self.directly_affected if item and item != self.path})
        self.test_files = sorted({item for item in self.test_files if item})
        self.transitively_affected = {str(path): int(depth) for path, depth in sorted(self.transitively_affected.items(), key=lambda pair: (int(pair[1]), str(pair[0]))) if path and path != self.path and int(depth) > 1}
        self.disclosures = sorted({item for item in self.disclosures if item})
        return self

    @property
    def directly_affected_paths(self) -> list[str]:
        return self.directly_affected

    @property
    def transitively_affected_paths(self) -> list[str]:
        return list(self.transitively_affected)

    @property
    def test_file_paths(self) -> list[str]:
        return self.test_files

    @property
    def confidence_label(self) -> ConfidenceLabel:
        return self.confidence

    @property
    def confidence_score(self) -> float:
        return {"low": 0.25, "medium": 0.6, "high": 0.95}[self.confidence]

    @property
    def how_computed(self) -> str:
        return self.computation

    @property
    def affected_with_depth(self) -> dict[str, int]:
        return {**{path: 1 for path in self.directly_affected}, **self.transitively_affected}

    @property
    def disclosure(self) -> list[str]:
        return self.disclosures


class SkippedFile(BaseModel):
    """A disclosed file omission, retained separately from the path list."""

    model_config = ConfigDict(extra="forbid")

    path: str
    reason: str
    detail: str = ""


class RefreshReport(BaseModel):
    """Observable outcome of one refresh coordinator run."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    repo_id: str = "default"
    status: Literal["succeeded", "disabled", "failed"] = "succeeded"
    added: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)
    read_files: list[str] = Field(default_factory=list)
    changed_count: int = Field(default=0, ge=0)
    snapshot_version: int | None = Field(default=None, ge=1)
    partial_index: bool = False
    disclosures: list[str] = Field(default_factory=list)
    error: str = ""

    @model_validator(mode="after")
    def _normalize_report(self) -> RefreshReport:
        for field_name in ("added", "updated", "removed", "skipped", "unchanged", "read_files"):
            value = sorted({str(item) for item in getattr(self, field_name) if item})
            setattr(self, field_name, value)
        self.disclosures = sorted({item for item in self.disclosures if item})
        self.reasons = {str(key): str(value) for key, value in sorted(self.reasons.items())}
        return self

    @property
    def changed(self) -> int:
        return self.changed_count

    @property
    def changed_paths(self) -> list[str]:
        return sorted([*self.added, *self.updated, *self.removed])

    @property
    def added_count(self) -> int:
        return len(self.added)

    @property
    def updated_count(self) -> int:
        return len(self.updated)

    @property
    def removed_count(self) -> int:
        return len(self.removed)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def skipped_reasons(self) -> dict[str, str]:
        return self.reasons

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


__all__ = [
    "ChangeImpact",
    "CodebaseSnapshot",
    "ConfidenceLabel",
    "DependencyEdge",
    "DependencyKind",
    "Hotspot",
    "ModuleRecord",
    "ParseStatus",
    "RefreshReport",
    "SkippedFile",
    "SymbolKind",
    "SymbolRef",
]
