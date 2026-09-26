"""Typed records for the static authority census.

This module is intentionally a *data contract*, not a policy engine.  The
auditor never imports Alpha, and it never changes a route, guard, or default;
it records what a source tree appears to permit so a human can review the
reasoning.  The vocabulary follows least-privilege and zero-trust guidance:
an action is described separately from the enforcement point that protects it,
and an unrecognised shape is represented explicitly instead of being coerced
into a safe-looking value.

References used by the package (titles/URLs are documentation pointers, not
copied text):

* OWASP Agentic Security Initiative, *Agentic AI – Threats and Mitigations*
  (2025), https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/
* OWASP GenAI Security Project / LLM Top 10,
  https://owasp.org/www-project-top-10-for-large-language-model-applications/
* NIST SP 800-207, *Zero Trust Architecture*,
  https://doi.org/10.6028/NIST.SP.800-207

The default-OFF auditor is a control-plane tool.  Like the OWASP guidance on
agentic action boundaries, it is safer to make an unclassified capability
visible than to grant it authority by omission.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class Reversibility(StrEnum):
    """Closed reversibility classes used by the classifier.

    The classes describe *what the action can do to the world*, not whether a
    particular implementation happens to have an undo button.  ``IRREVERSIBLE``
    is therefore the conservative bucket for arbitrary code execution and for
    operations whose recovery cannot be proven statically.
    """

    REVERSIBLE = "reversible"
    DESTRUCTIVE_LOCAL = "destructive_local"
    EXTERNAL_WORLD = "external_world"
    FINANCIAL = "financial"
    PRIVACY = "privacy"
    IRREVERSIBLE = "irreversible"


class Externality(StrEnum):
    """Closed locality classes used alongside reversibility."""

    LOCAL = "local"
    EXTERNAL = "external"
    FINANCIAL = "financial"
    PRIVACY = "privacy"
    UNKNOWN = "unknown"


class GatePosture(StrEnum):
    """Default behaviour of an enforcement point when its check is not decisive."""

    DEFAULT_ALLOW = "default_allow"
    DEFAULT_DENY = "default_deny"


class GateKind(StrEnum):
    """The kinds of enforcement points the census recognises."""

    DECORATOR = "decorator"
    MIDDLEWARE = "middleware"
    TOOL_GUARD = "tool_guard"
    APPROVAL = "approval"
    CONFIG_FLAG = "config_flag"
    SANDBOX = "sandbox"
    AUTHORIZATION = "authorization"
    UNKNOWN = "unknown"


class Verdict(StrEnum):
    """The closed set of unattended-operation answers.

    ``UNKNOWN`` is a blocking answer.  It is intentionally not a synonym for
    ``SAFE_UNATTENDED`` and callers must not filter it out of safety counts.
    """

    BLOCKED_BY_DESIGN = "blocked_by_design"
    AUTO_REPLACEABLE = "auto_replaceable"
    REQUIRES_OPERATOR_TOKEN = "requires_operator_token"
    SAFE_UNATTENDED = "safe_unattended"
    UNKNOWN = "unknown"


class UnknownFinding(BaseModel):
    """A source location the static census could not classify confidently."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(description="Repository-relative path that produced the gap.")
    line: int = Field(default=0, ge=0, description="1-based source line, or 0 for a whole file.")
    kind: str = Field(description="Unknown class, e.g. parse_error or dynamic_registration.")
    reason: str = Field(description="Why static analysis could not decide.")
    rule_id: str = Field(description="Reviewable rule that made the conservative decision.")
    location: str = Field(default="", description="Human-readable path:line location.")

    def model_post_init(self, _context: Any) -> None:
        if not self.location:
            object.__setattr__(self, "location", f"{self.path}:{self.line}" if self.line else self.path)


class Capability(BaseModel):
    """One concrete action a source surface appears able to perform."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: str = Field(min_length=1, description="Stable, repository-relative capability id.")
    description: str = Field(description="What the action is, in operator language.")
    source_file: str = Field(
        validation_alias=AliasChoices("source_file", "file"),
        description="Repository-relative source file.",
    )
    source_line: int = Field(
        default=0,
        validation_alias=AliasChoices("source_line", "line"),
        ge=0,
        description="1-based source line, or 0 when the finding is file-wide.",
    )
    concrete_action: str = Field(
        validation_alias=AliasChoices("concrete_action", "action"),
        description="The literal operation/verb the capability permits.",
    )
    reversibility_class: Reversibility = Field(
        validation_alias=AliasChoices("reversibility_class", "reversibility"),
        description="Closed reversibility classification.",
    )
    currently_gated: bool = Field(
        default=False,
        validation_alias=AliasChoices("currently_gated", "gated"),
        description="Whether a statically visible enforcement point covers it.",
    )
    gating: str | None = Field(
        default=None,
        validation_alias=AliasChoices("gating", "gate_how"),
        description="How the action is gated, or why no gate was found.",
    )
    externality: Externality = Field(default=Externality.LOCAL, description="Whether the action leaves the process/host.")
    classification_rule_id: str = Field(
        default="UNK-002",
        validation_alias=AliasChoices("classification_rule_id", "rule_id"),
        description="Explicit rule that produced the reversibility/externality decision.",
    )
    tags: tuple[str, ...] = Field(default_factory=tuple, description="Searchable capability tags.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Non-authoritative evidence hints.")

    @model_validator(mode="before")
    @classmethod
    def _split_source_alias(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "source_file" in value or "file" in value or "source" not in value:
            return value
        data = dict(value)
        source = str(data.pop("source"))
        file_part, separator, line_part = source.rpartition(":")
        if separator and line_part.isdigit():
            data["source_file"] = file_part
            data["source_line"] = int(line_part)
        else:
            data["source_file"] = source
        return data

    @property
    def action(self) -> str:
        """Alias for :attr:`concrete_action` used by report consumers."""

        return self.concrete_action

    @property
    def reversibility(self) -> Reversibility:
        """Short alias for :attr:`reversibility_class`."""

        return self.reversibility_class

    @property
    def gated(self) -> bool:
        """Short alias for :attr:`currently_gated`."""

        return self.currently_gated

    @property
    def gate_how(self) -> str | None:
        """Short alias for :attr:`gating`."""

        return self.gating

    @property
    def rule_id(self) -> str:
        """Short alias for :attr:`classification_rule_id`."""

        return self.classification_rule_id

    @property
    def source(self) -> str:
        """``path:line`` source location."""

        return f"{self.source_file}:{self.source_line}" if self.source_line else self.source_file

    @property
    def source_location(self) -> str:
        """Explicit alias for :attr:`source`."""

        return self.source

    @property
    def is_destructive(self) -> bool:
        """Whether coverage analysis must demand a default-deny gate."""

        return self.reversibility_class is not Reversibility.REVERSIBLE


class Gate(BaseModel):
    """An enforcement point that can permit or deny a capability."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: str = Field(min_length=1, description="Stable gate id.")
    enforcement_point: str = Field(
        validation_alias=AliasChoices("enforcement_point", "point"),
        description="Decorator, middleware, guard, flag, or function that enforces policy.",
    )
    kind: GateKind = Field(default=GateKind.UNKNOWN, validation_alias=AliasChoices("kind", "type"))
    default_posture: GatePosture = Field(
        default=GatePosture.DEFAULT_ALLOW,
        validation_alias=AliasChoices("default_posture", "posture"),
        description="Posture when the check is absent, empty, or non-decisive.",
    )
    protects: tuple[str, ...] = Field(
        default_factory=tuple,
        validation_alias=AliasChoices("protects", "what_it_protects"),
        description="Capability ids this gate is statically associated with.",
    )
    source_file: str = Field(validation_alias=AliasChoices("source_file", "file"))
    source_line: int = Field(default=0, validation_alias=AliasChoices("source_line", "line"), ge=0)
    rule_id: str = Field(default="GATE-UNK-001", description="Rule used to infer posture and effectiveness.")
    description: str = Field(default="", description="What the gate protects and how it is wired.")
    can_fail: bool = Field(default=True, description="False when static evidence says the check cannot deny.")
    ineffective_reason: str | None = Field(default=None, description="Why a non-failing gate is ineffective.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Non-authoritative evidence hints.")

    @model_validator(mode="before")
    @classmethod
    def _split_source_alias(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "source_file" in value or "file" in value or "source" not in value:
            return value
        data = dict(value)
        source = str(data.pop("source"))
        file_part, separator, line_part = source.rpartition(":")
        if separator and line_part.isdigit():
            data["source_file"] = file_part
            data["source_line"] = int(line_part)
        else:
            data["source_file"] = source
        return data

    @property
    def posture(self) -> GatePosture:
        """Short alias for :attr:`default_posture`."""

        return self.default_posture

    @property
    def what_it_protects(self) -> tuple[str, ...]:
        """Short alias for :attr:`protects`."""

        return self.protects

    @property
    def source(self) -> str:
        """``path:line`` source location."""

        return f"{self.source_file}:{self.source_line}" if self.source_line else self.source_file


class AuthorityRecord(BaseModel):
    """A capability, the gate found for it, and the unattended verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    capability: Capability
    gate: Gate | None = Field(default=None, description="Enforcement point, or None when ungated.")
    verdict: Verdict = Field(
        default=Verdict.UNKNOWN,
        validation_alias=AliasChoices("verdict", "unattended_verdict"),
    )
    verdict_rule_id: str = Field(default="VERDICT-UNK-001", description="Rule used for the unattended verdict.")
    reason: str = Field(default="", description="Human-readable rationale, including the gate when present.")

    @property
    def capability_id(self) -> str:
        return self.capability.id

    @property
    def gated(self) -> bool:
        return self.capability.currently_gated

    @property
    def source(self) -> str:
        return self.capability.source

    @property
    def is_unknown(self) -> bool:
        return self.verdict is Verdict.UNKNOWN


class CoverageSummary(BaseModel):
    """Coverage of destructive capabilities by effective default-deny gates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    denominator: int = Field(default=0, ge=0, description="Destructive (including unknown) capabilities considered.")
    numerator: int = Field(default=0, ge=0, description="Destructive capabilities protected by an effective default-deny gate.")
    percentage: float = Field(default=0.0, ge=0.0, le=100.0, description="numerator / denominator * 100.")
    destructive_capability_count: int = Field(default=0, ge=0)
    gated_destructive_capability_count: int = Field(default=0, ge=0)
    ungated_destructive: tuple[str, ...] = Field(default_factory=tuple)
    gates_protecting_nothing: tuple[str, ...] = Field(default_factory=tuple)
    ineffective_gates: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def coverage_percentage(self) -> float:
        return self.percentage

    @property
    def coverage_denominator(self) -> int:
        return self.denominator

    def describe(self) -> str:
        """Return the percentage with its denominator stated explicitly."""

        return f"{self.percentage:.1f}% ({self.numerator}/{self.denominator} destructive capabilities)"


class AuditGap(BaseModel):
    """A report-level gap that is not itself a capability record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    path: str
    line: int = Field(default=0, ge=0)
    reason: str
    rule_id: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


class AuditReport(BaseModel):
    """Deterministic census output.

    ``revision`` is injected by the caller.  There is intentionally no
    ``generated_at`` field: a wall-clock value would make two identical scans
    differ and would make review diffs noisy.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    revision: str = Field(default="unrevised", description="Injected commit/revision string.")
    records: tuple[AuthorityRecord, ...] = Field(default_factory=tuple)
    gates: tuple[Gate, ...] = Field(default_factory=tuple)
    unknowns: tuple[UnknownFinding, ...] = Field(default_factory=tuple)
    gaps: tuple[AuditGap, ...] = Field(default_factory=tuple)
    verdict_counts: dict[str, int] = Field(default_factory=dict)
    coverage: CoverageSummary = Field(default_factory=CoverageSummary)
    ungated_destructive_capabilities: tuple[str, ...] = Field(default_factory=tuple)
    ineffective_gates: tuple[str, ...] = Field(default_factory=tuple)
    scanned_files: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def capabilities(self) -> tuple[Capability, ...]:
        return tuple(record.capability for record in self.records)

    @property
    def counts(self) -> dict[str, int]:
        return dict(self.verdict_counts)

    @property
    def coverage_percentage(self) -> float:
        return self.coverage.percentage

    @property
    def coverage_denominator(self) -> int:
        return self.coverage.denominator

    @property
    def unknown_paths(self) -> tuple[str, ...]:
        return tuple(sorted({item.path for item in self.unknowns}))

    @property
    def capability_count(self) -> int:
        return len(self.records)

    @property
    def gate_count(self) -> int:
        return len(self.gates)

    @property
    def has_unknown(self) -> bool:
        return any(record.verdict is Verdict.UNKNOWN for record in self.records) or bool(self.unknowns)

    def has_triage_findings(self, *, strict: bool = False) -> bool:
        """Return whether the report contains work an operator must triage.

        Unknowns are findings by default because an unknown is a blocking
        answer.  ``strict`` is retained as an explicit switch for callers that
        want the same decision expressed at the CLI boundary.
        """

        return bool(self.unknowns or self.ungated_destructive_capabilities or self.ineffective_gates or self.gaps or (strict and self.has_unknown) or any(record.verdict is Verdict.UNKNOWN for record in self.records))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready, ordered mapping."""

        return self.model_dump(mode="json")


__all__ = [
    "AuditGap",
    "AuditReport",
    "AuthorityRecord",
    "Capability",
    "CoverageSummary",
    "Externality",
    "Gate",
    "GateKind",
    "GatePosture",
    "Reversibility",
    "UnknownFinding",
    "Verdict",
]
