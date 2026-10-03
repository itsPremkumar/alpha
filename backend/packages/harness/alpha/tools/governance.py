"""Per-tool governance registry: risk class, side effects, reversibility.

The discovery catalog has always *disclosed* optional per-tool
metadata (``discovery_risk_level``, ``discovery_permissions``,
…) and has always been honest that it is not an enforcement
mechanism — its docstring states that Alpha has no per-tool
risk/permission registry and therefore defaults every
unannotated tool to risk ``low``. This module is that registry.

It is deliberately additive and fail-closed:

* A tool that declares nothing keeps today's exact behaviour
  (first-party default: ``READ`` / ``AUTO`` / ``REVERSIBLE``).
* A tool from an **untrusted source** (MCP server, client-supplied
  tool) never gets to classify itself. Its self-declared
  ``governance_*`` metadata is ignored — it is attacker-controlled
  input, exactly like its parameter schema — and it defaults to
  the elevated class ``WRITE`` / ``ASK`` / ``UNKNOWN`` reversibility.
* Only an **operator** entry (``config.yaml -> tool_governance``)
  classifies an untrusted tool more precisely, and it wins over
  every other source.
* Every malformed declaration raises :class:`GovernanceError`.
  A typo in a governance key or value is a configuration error,
  never a silent fallback to ``low``.

Resolution order for :meth:`GovernanceRegistry.entry_for`:

1. operator config entry (exact tool name), any source;
2. untrusted source -> elevated default (metadata ignored);
3. first-party tool ``governance_*`` metadata;
4. first-party default.

:meth:`GovernanceRegistry.evaluate` turns an entry into an
AUTO/ASK/BLOCK decision. The declared ``confirmation`` is
authoritative, with one defensive escalation: an entry whose
provenance is ``elevated`` (nothing was declared, we inferred
from the source) can never be AUTO for a class above ``WRITE``.

The registry does not intercept tool calls. It is the single
place a future permission boundary consults; until that boundary
exists, the disclosure path is ``alpha.tools.discovery.catalog``,
which maps a governed risk class onto the catalog's
``low``/``medium``/``high``/``critical`` scale.

Config section (plain mapping; ``AppConfig`` is ``extra="allow"``,
so the section is additive and needs no schema migration)::

    tool_governance:
      web_fetch:
        risk_class: external          # read | write | execute | external | destructive
        permissions: [network]
        side_effects: [http_request]
        reversibility: reversible     # reversible | irreversible | unknown
        timeout_seconds: 30
        retry:
          max_attempts: 2
          backoff_seconds: 1.0
          retryable_errors: [TimeoutError]
        verification_method: status   # none | existence | status | test | artifact | manual
        confirmation: ask             # auto | ask | block
        notes: "outbound HTTP"
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: Metadata keys a first-party tool may declare on ``BaseTool.metadata``.
GOVERNANCE_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "governance_risk_class",
        "governance_permissions",
        "governance_side_effects",
        "governance_reversibility",
        "governance_timeout_seconds",
        "governance_retry_policy",
        "governance_verification_method",
        "governance_confirmation",
    }
)

#: Config keys accepted per tool entry. Anything else fails closed.
_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "risk_class",
        "permissions",
        "side_effects",
        "reversibility",
        "timeout_seconds",
        "retry",
        "verification_method",
        "confirmation",
        "notes",
    }
)

#: Config keys accepted inside a ``retry:`` block.
_RETRY_KEYS: frozenset[str] = frozenset({"max_attempts", "backoff_seconds", "retryable_errors"})


class GovernanceError(ValueError):
    """A governance declaration is malformed. Fail closed, never guess."""


class RiskClass(StrEnum):
    """What a tool can do to the world."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    EXTERNAL = "external"
    DESTRUCTIVE = "destructive"


class Reversibility(StrEnum):
    """Whether the tool's effects can be undone."""

    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"
    UNKNOWN = "unknown"


class ConfirmationPolicy(StrEnum):
    """Human confirmation required before the tool runs."""

    AUTO = "auto"
    ASK = "ask"
    BLOCK = "block"


class VerificationMethod(StrEnum):
    """How a caller verifies the tool actually did what it reported."""

    NONE = "none"
    EXISTENCE = "existence"
    STATUS = "status"
    TEST = "test"
    ARTIFACT = "artifact"
    MANUAL = "manual"


#: Risk classes whose effects reach beyond this machine.
_EFFECTFUL_CLASSES: frozenset[RiskClass] = frozenset({RiskClass.WRITE, RiskClass.EXECUTE, RiskClass.EXTERNAL, RiskClass.DESTRUCTIVE})

#: Mapping onto the discovery catalog's disclosure scale.
_RISK_LEVEL_BY_CLASS: dict[RiskClass, str] = {
    RiskClass.READ: "low",
    RiskClass.WRITE: "medium",
    RiskClass.EXECUTE: "high",
    RiskClass.EXTERNAL: "high",
    RiskClass.DESTRUCTIVE: "critical",
}


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry policy. Unbounded retry loops are a configuration error."""

    max_attempts: int = 1
    backoff_seconds: float = 0.0
    retryable_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise GovernanceError(f"retry.max_attempts must be >= 1, got {self.max_attempts}")
        if self.backoff_seconds < 0:
            raise GovernanceError(f"retry.backoff_seconds must be >= 0, got {self.backoff_seconds}")


@dataclass(frozen=True)
class ToolGovernance:
    """The governed declaration for one tool."""

    name: str
    risk_class: RiskClass = RiskClass.READ
    permissions: frozenset[str] = frozenset()
    side_effects: frozenset[str] = frozenset()
    reversibility: Reversibility = Reversibility.UNKNOWN
    timeout_seconds: float | None = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    verification_method: VerificationMethod = VerificationMethod.NONE
    confirmation: ConfirmationPolicy = ConfirmationPolicy.AUTO
    #: Where the declaration came from: ``operator`` (config), ``declared``
    #: (first-party tool metadata), ``elevated`` (untrusted-source default)
    #: or ``default`` (first-party unannotated).
    provenance: str = "default"
    notes: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise GovernanceError("tool governance requires a non-empty tool name")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise GovernanceError(f"timeout_seconds must be positive or null, got {self.timeout_seconds}")
        if self.provenance not in {"operator", "declared", "elevated", "default"}:
            raise GovernanceError(f"unknown provenance {self.provenance!r}")


@dataclass(frozen=True)
class GovernanceDecision:
    """The AUTO/ASK/BLOCK verdict for one tool invocation."""

    action: ConfirmationPolicy
    governance: ToolGovernance
    reasons: tuple[str, ...]


def _parse_enum(value: Any, enum: type[StrEnum], label: str) -> StrEnum:
    if isinstance(value, enum):
        return value
    if isinstance(value, str):
        try:
            return enum(value.strip().lower())
        except ValueError:
            pass
    valid = ", ".join(member.value for member in enum)
    raise GovernanceError(f"{label} must be one of: {valid}; got {value!r}")


def _parse_string_set(value: Any, label: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list | tuple | set | frozenset):
        raise GovernanceError(f"{label} must be a list of strings, got {type(value).__name__}")
    items: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise GovernanceError(f"{label} entries must be non-empty strings, got {item!r}")
        items.add(item.strip())
    return frozenset(items)


def _parse_retry(value: Any) -> RetryPolicy:
    if value is None:
        return RetryPolicy()
    if not isinstance(value, Mapping):
        raise GovernanceError(f"retry must be a mapping, got {type(value).__name__}")
    unknown = set(value) - _RETRY_KEYS
    if unknown:
        raise GovernanceError(f"unknown retry key(s): {sorted(unknown)}")
    max_attempts = value.get("max_attempts", 1)
    backoff = value.get("backoff_seconds", 0.0)
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool):
        raise GovernanceError(f"retry.max_attempts must be an integer, got {max_attempts!r}")
    if not isinstance(backoff, int | float) or isinstance(backoff, bool):
        raise GovernanceError(f"retry.backoff_seconds must be a number, got {backoff!r}")
    return RetryPolicy(
        max_attempts=max_attempts,
        backoff_seconds=float(backoff),
        retryable_errors=tuple(_parse_string_set(value.get("retryable_errors"), "retry.retryable_errors")),
    )


def _entry_from_mapping(name: str, spec: Any, *, provenance: str) -> ToolGovernance:
    if not isinstance(spec, Mapping):
        raise GovernanceError(f"tool_governance[{name!r}] must be a mapping, got {type(spec).__name__}")
    unknown = set(spec) - _CONFIG_KEYS
    if unknown:
        raise GovernanceError(f"unknown tool_governance[{name!r}] key(s): {sorted(unknown)}")
    timeout = spec.get("timeout_seconds")
    if timeout is not None:
        if not isinstance(timeout, int | float) or isinstance(timeout, bool):
            raise GovernanceError(f"tool_governance[{name!r}].timeout_seconds must be a number, got {timeout!r}")
        timeout = float(timeout)
    return ToolGovernance(
        name=name,
        risk_class=_parse_enum(spec.get("risk_class", RiskClass.READ), RiskClass, f"tool_governance[{name!r}].risk_class"),
        permissions=_parse_string_set(spec.get("permissions"), f"tool_governance[{name!r}].permissions"),
        side_effects=_parse_string_set(spec.get("side_effects"), f"tool_governance[{name!r}].side_effects"),
        reversibility=_parse_enum(spec.get("reversibility", Reversibility.UNKNOWN), Reversibility, f"tool_governance[{name!r}].reversibility"),
        timeout_seconds=timeout,
        retry_policy=_parse_retry(spec.get("retry")),
        verification_method=_parse_enum(
            spec.get("verification_method", VerificationMethod.NONE),
            VerificationMethod,
            f"tool_governance[{name!r}].verification_method",
        ),
        confirmation=_parse_enum(
            spec.get("confirmation", ConfirmationPolicy.AUTO),
            ConfirmationPolicy,
            f"tool_governance[{name!r}].confirmation",
        ),
        provenance=provenance,
        notes=str(spec.get("notes", "")),
    )


def _entry_from_metadata(name: str, metadata: Mapping[str, Any]) -> ToolGovernance:
    """Build an entry from a first-party tool's declared ``governance_*`` metadata."""
    retry_raw = metadata.get("governance_retry_policy")
    timeout = metadata.get("governance_timeout_seconds")
    if timeout is not None:
        if not isinstance(timeout, int | float) or isinstance(timeout, bool) or timeout <= 0:
            raise GovernanceError(f"governance_timeout_seconds for {name!r} must be a positive number or null, got {timeout!r}")
        timeout = float(timeout)
    return ToolGovernance(
        name=name,
        risk_class=_parse_enum(metadata.get("governance_risk_class", RiskClass.READ), RiskClass, f"governance_risk_class for {name!r}"),
        permissions=_parse_string_set(metadata.get("governance_permissions"), f"governance_permissions for {name!r}"),
        side_effects=_parse_string_set(metadata.get("governance_side_effects"), f"governance_side_effects for {name!r}"),
        reversibility=_parse_enum(
            metadata.get("governance_reversibility", Reversibility.UNKNOWN),
            Reversibility,
            f"governance_reversibility for {name!r}",
        ),
        timeout_seconds=timeout,
        retry_policy=_parse_retry(retry_raw),
        verification_method=_parse_enum(
            metadata.get("governance_verification_method", VerificationMethod.NONE),
            VerificationMethod,
            f"governance_verification_method for {name!r}",
        ),
        confirmation=_parse_enum(
            metadata.get("governance_confirmation", ConfirmationPolicy.AUTO),
            ConfirmationPolicy,
            f"governance_confirmation for {name!r}",
        ),
        provenance="declared",
    )


class GovernanceRegistry:
    """Operator-configured per-tool governance, with safe defaults."""

    def __init__(self, entries: Mapping[str, ToolGovernance] | None = None) -> None:
        self._entries: dict[str, ToolGovernance] = dict(entries or {})

    @classmethod
    def from_mapping(cls, spec: Mapping[str, Any] | None) -> GovernanceRegistry:
        """Parse the ``tool_governance`` config section. ``None`` is an empty registry.

        Every malformed entry raises :class:`GovernanceError` — a typo is a
        startup error, not a silent fallback.
        """
        if spec is None:
            return cls()
        if not isinstance(spec, Mapping):
            raise GovernanceError(f"tool_governance must be a mapping, got {type(spec).__name__}")
        entries: dict[str, ToolGovernance] = {}
        for name, tool_spec in spec.items():
            if not isinstance(name, str) or not name.strip():
                raise GovernanceError(f"tool_governance keys must be non-empty tool names, got {name!r}")
            entries[name.strip()] = _entry_from_mapping(name.strip(), tool_spec, provenance="operator")
        return cls(entries)

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    @property
    def entries(self) -> tuple[tuple[str, ToolGovernance], ...]:
        """Every operator entry as ``(name, governance)``, name-sorted."""
        return tuple(sorted(self._entries.items()))

    def get(self, name: str) -> ToolGovernance | None:
        """The operator entry for *name*, or ``None``."""
        return self._entries.get(name)

    def entry_for(
        self,
        name: str,
        *,
        untrusted_source: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> ToolGovernance:
        """Resolve the governance entry for *name*.

        Precedence: operator config entry, then the untrusted-source
        elevated default (which ignores the tool's self-declared
        metadata — it is attacker-controlled input), then first-party
        declared metadata, then the first-party default.
        """
        operator_entry = self._entries.get(name)
        if operator_entry is not None:
            return operator_entry
        if untrusted_source:
            # An MCP or client-supplied tool may not classify itself: its
            # self-declared governance metadata is attacker-controlled input
            # and is ignored here, exactly like its parameter schema.
            return ToolGovernance(
                name=name,
                risk_class=RiskClass.WRITE,
                confirmation=ConfirmationPolicy.ASK,
                reversibility=Reversibility.UNKNOWN,
                provenance="elevated",
            )
        if metadata:
            declared_keys = GOVERNANCE_METADATA_KEYS.intersection(metadata)
            if declared_keys:
                return _entry_from_metadata(name, metadata)
        return ToolGovernance(
            name=name,
            risk_class=RiskClass.READ,
            reversibility=Reversibility.REVERSIBLE,
            provenance="default",
        )

    def evaluate(
        self,
        name: str,
        *,
        untrusted_source: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> GovernanceDecision:
        """Resolve the entry and turn it into an AUTO/ASK/BLOCK decision."""
        governance = self.entry_for(name, untrusted_source=untrusted_source, metadata=metadata)
        reasons = [f"risk_class={governance.risk_class.value}"]
        if governance.side_effects:
            reasons.append("side_effects=" + ",".join(sorted(governance.side_effects)))
        if governance.reversibility is Reversibility.IRREVERSIBLE:
            reasons.append("reversibility=irreversible")
        if governance.provenance == "elevated":
            reasons.append("provenance=elevated:untrusted_source")
        action = governance.confirmation
        # Defensive escalation: an inferred (elevated) entry can never be
        # AUTO for a class above WRITE, whatever a caller constructed.
        if action is ConfirmationPolicy.AUTO and governance.provenance == "elevated" and governance.risk_class not in (RiskClass.READ, RiskClass.WRITE):
            action = ConfirmationPolicy.ASK
            reasons.append("escalated:elevated_entry_above_write")
        return GovernanceDecision(action=action, governance=governance, reasons=tuple(reasons))

    @staticmethod
    def risk_level(governance: ToolGovernance) -> str:
        """Map a risk class onto the discovery catalog's disclosure scale."""
        return _RISK_LEVEL_BY_CLASS[governance.risk_class]


def governance_from_tool_metadata(tool: Any) -> ToolGovernance | None:
    """The declared governance entry for a tool object, or ``None``.

    Reads only the tool's own ``governance_*`` metadata keys. Returns
    ``None`` when the tool declares nothing, so callers keep their
    existing default path. Malformed declarations raise
    :class:`GovernanceError` rather than being skipped.
    """
    metadata = getattr(tool, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    if not GOVERNANCE_METADATA_KEYS.intersection(metadata):
        return None
    name = getattr(tool, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise GovernanceError("a tool declaring governance metadata must have a name")
    return _entry_from_metadata(name, metadata)


def risk_level_for_tool(tool: Any) -> str | None:
    """The governed risk level for a tool object, or ``None`` when unannotated.

    Disclosure path only: maps the tool's declared ``governance_risk_class``
    onto the catalog's ``low``/``medium``/``high``/``critical`` scale.
    """
    entry = governance_from_tool_metadata(tool)
    if entry is None:
        return None
    return GovernanceRegistry.risk_level(entry)


__all__ = [
    "GOVERNANCE_METADATA_KEYS",
    "ConfirmationPolicy",
    "GovernanceDecision",
    "GovernanceError",
    "GovernanceRegistry",
    "Reversibility",
    "RetryPolicy",
    "RiskClass",
    "ToolGovernance",
    "VerificationMethod",
    "governance_from_tool_metadata",
    "risk_level_for_tool",
]
