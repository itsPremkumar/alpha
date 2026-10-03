"""Tests for the per-tool governance registry (``alpha.tools.governance``).

Covers the registry contract the discovery catalog previously stated was
missing: risk class, permissions, side effects, reversibility, timeout,
bounded retry policy, verification method, and AUTO/ASK/BLOCK policy
evaluation — plus the trust rules that make it safe:

* an untrusted source (MCP / client-supplied) never classifies itself;
* an operator config entry wins over every tool-side declaration;
* every malformed declaration fails closed with ``GovernanceError``;
* a tool that declares nothing keeps the pre-registry behaviour exactly.
"""

from __future__ import annotations

from typing import Any

import pytest

from alpha.tools.discovery.catalog import resolve_risk_level
from alpha.tools.governance import (
    ConfirmationPolicy,
    GovernanceError,
    GovernanceRegistry,
    RetryPolicy,
    Reversibility,
    RiskClass,
    ToolGovernance,
    VerificationMethod,
    governance_from_tool_metadata,
    risk_level_for_tool,
)


class _StubTool:
    """Minimal tool double: the governance and catalog paths read only these."""

    def __init__(self, metadata: dict[str, Any] | None = None, name: str = "stub_tool") -> None:
        self.metadata = metadata or {}
        self.name = name


# ---------------------------------------------------------------- defaults --


def test_first_party_default_is_safe_read() -> None:
    registry = GovernanceRegistry()
    entry = registry.entry_for("some_tool")
    assert entry.risk_class is RiskClass.READ
    assert entry.confirmation is ConfirmationPolicy.AUTO
    assert entry.reversibility is Reversibility.REVERSIBLE
    assert entry.provenance == "default"
    assert registry.risk_level(entry) == "low"


def test_unannotated_tool_keeps_catalog_default() -> None:
    assert resolve_risk_level(_StubTool()) == "low"
    assert risk_level_for_tool(_StubTool()) is None
    assert governance_from_tool_metadata(_StubTool()) is None


def test_untrusted_source_is_elevated_and_cannot_classify_itself() -> None:
    registry = GovernanceRegistry()
    # The tool *claims* to be a harmless auto-read; its source is untrusted,
    # so the claim is attacker-controlled input and must be ignored.
    malicious_metadata = {
        "governance_risk_class": "read",
        "governance_confirmation": "auto",
        "governance_reversibility": "reversible",
    }
    entry = registry.entry_for("evil_mcp_tool", untrusted_source=True, metadata=malicious_metadata)
    assert entry.risk_class is RiskClass.WRITE
    assert entry.confirmation is ConfirmationPolicy.ASK
    assert entry.reversibility is Reversibility.UNKNOWN
    assert entry.provenance == "elevated"
    assert registry.risk_level(entry) == "medium"


# --------------------------------------------------------- declared metadata --


def test_first_party_metadata_declaration_is_honored() -> None:
    registry = GovernanceRegistry()
    metadata = {
        "governance_risk_class": "destructive",
        "governance_permissions": ["filesystem"],
        "governance_side_effects": ["delete"],
        "governance_reversibility": "irreversible",
        "governance_timeout_seconds": 30,
        "governance_retry_policy": {"max_attempts": 2, "backoff_seconds": 1.5},
        "governance_verification_method": "existence",
        "governance_confirmation": "block",
    }
    entry = registry.entry_for("rm_tool", metadata=metadata)
    assert entry.risk_class is RiskClass.DESTRUCTIVE
    assert entry.permissions == frozenset({"filesystem"})
    assert entry.side_effects == frozenset({"delete"})
    assert entry.reversibility is Reversibility.IRREVERSIBLE
    assert entry.timeout_seconds == 30.0
    assert entry.retry_policy == RetryPolicy(max_attempts=2, backoff_seconds=1.5)
    assert entry.verification_method is VerificationMethod.EXISTENCE
    assert entry.confirmation is ConfirmationPolicy.BLOCK
    assert entry.provenance == "declared"
    assert registry.risk_level(entry) == "critical"


def test_catalog_resolve_risk_level_maps_governed_class() -> None:
    assert resolve_risk_level(_StubTool({"governance_risk_class": "read"})) == "low"
    assert resolve_risk_level(_StubTool({"governance_risk_class": "write"})) == "medium"
    assert resolve_risk_level(_StubTool({"governance_risk_class": "execute"})) == "high"
    assert resolve_risk_level(_StubTool({"governance_risk_class": "external"})) == "high"
    assert resolve_risk_level(_StubTool({"governance_risk_class": "destructive"})) == "critical"


def test_governed_class_takes_precedence_over_legacy_level() -> None:
    metadata = {"discovery_risk_level": "medium", "governance_risk_class": "destructive"}
    assert resolve_risk_level(_StubTool(metadata)) == "critical"


def test_legacy_discovery_level_still_wins_when_no_governance() -> None:
    assert resolve_risk_level(_StubTool({"discovery_risk_level": "high"})) == "high"


# ------------------------------------------------------------- operator config --


def test_operator_config_entry_wins_over_source_and_metadata() -> None:
    registry = GovernanceRegistry.from_mapping(
        {
            "bash": {
                "risk_class": "execute",
                "side_effects": ["process", "filesystem"],
                "reversibility": "unknown",
                "confirmation": "ask",
                "verification_method": "status",
            }
        }
    )
    entry = registry.entry_for(
        "bash",
        untrusted_source=True,
        metadata={"governance_risk_class": "read", "governance_confirmation": "auto"},
    )
    assert entry.provenance == "operator"
    assert entry.risk_class is RiskClass.EXECUTE
    assert entry.confirmation is ConfirmationPolicy.ASK
    assert entry.side_effects == frozenset({"process", "filesystem"})
    assert entry.verification_method is VerificationMethod.STATUS


def test_from_mapping_parses_full_section() -> None:
    registry = GovernanceRegistry.from_mapping(
        {
            "web_fetch": {
                "risk_class": "external",
                "permissions": ["network"],
                "side_effects": ["http_request"],
                "reversibility": "reversible",
                "timeout_seconds": 30,
                "retry": {"max_attempts": 3, "backoff_seconds": 2.0, "retryable_errors": ["TimeoutError"]},
                "verification_method": "status",
                "confirmation": "auto",
                "notes": "outbound HTTP",
            }
        }
    )
    entry = registry.get("web_fetch")
    assert entry is not None
    assert entry.risk_class is RiskClass.EXTERNAL
    assert entry.permissions == frozenset({"network"})
    assert entry.timeout_seconds == 30.0
    assert entry.retry_policy.retryable_errors == ("TimeoutError",)
    assert entry.notes == "outbound HTTP"
    assert registry.tool_names == ("web_fetch",)


@pytest.mark.parametrize("spec", [None, {}])
def test_from_mapping_accepts_none_and_empty(spec: Any) -> None:
    registry = GovernanceRegistry.from_mapping(spec)
    assert registry.tool_names == ()
    assert registry.entry_for("anything").provenance == "default"


@pytest.mark.parametrize(
    "spec",
    [
        {"tool": {"risk_clas": "read"}},  # unknown key (typo) fails closed
        {"tool": {"risk_class": "nope"}},  # unknown enum value
        {"tool": {"confirmation": "maybe"}},
        {"tool": {"reversibility": "sometimes"}},
        {"tool": {"verification_method": " vibes"}},
        {"tool": {"timeout_seconds": -5}},
        {"tool": {"timeout_seconds": "soon"}},
        {"tool": {"retry": {"max_attempts": 0}}},
        {"tool": {"retry": {"max_attempts": "many"}}},
        {"tool": {"retry": {"backoff_seconds": -1}}},
        {"tool": {"retry": {"unknown_key": 1}}},
        {"tool": {"permissions": "network"}},  # not a list
        {"tool": {"permissions": [""]}},  # empty string entry
        {"tool": "execute"},  # not a mapping
        {"": {"risk_class": "read"}},  # empty tool name
        {"tool": {"side_effects": [1, 2]}},  # non-string entry
    ],
)
def test_malformed_config_fails_closed(spec: dict[str, Any]) -> None:
    with pytest.raises(GovernanceError):
        GovernanceRegistry.from_mapping(spec)


def test_from_mapping_rejects_non_mapping_root() -> None:
    with pytest.raises(GovernanceError):
        GovernanceRegistry.from_mapping(["web_fetch"])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "metadata",
    [
        {"governance_risk_class": "nope"},
        {"governance_confirmation": "perhaps"},
        {"governance_timeout_seconds": -1},
        {"governance_timeout_seconds": "later"},
        {"governance_retry_policy": {"max_attempts": 0}},
        {"governance_side_effects": "delete"},
    ],
)
def test_malformed_tool_metadata_fails_closed(metadata: dict[str, Any]) -> None:
    registry = GovernanceRegistry()
    with pytest.raises(GovernanceError):
        registry.entry_for("bad_tool", metadata=metadata)


def test_governance_from_tool_metadata_requires_a_name() -> None:
    tool = _StubTool({"governance_risk_class": "read"}, name="")
    with pytest.raises(GovernanceError):
        governance_from_tool_metadata(tool)


# ------------------------------------------------------------------- decisions --


def test_evaluate_passes_declared_confirmation_through() -> None:
    registry = GovernanceRegistry.from_mapping(
        {
            "read_only": {"risk_class": "read", "confirmation": "auto"},
            "writer": {"risk_class": "write", "confirmation": "ask"},
            "nuclear": {"risk_class": "destructive", "confirmation": "block"},
        }
    )
    auto = registry.evaluate("read_only")
    assert auto.action is ConfirmationPolicy.AUTO
    assert any(r.startswith("risk_class=read") for r in auto.reasons)
    ask = registry.evaluate("writer")
    assert ask.action is ConfirmationPolicy.ASK
    assert any("risk_class=write" in r for r in ask.reasons)
    block = registry.evaluate("nuclear")
    assert block.action is ConfirmationPolicy.BLOCK
    assert any("risk_class=destructive" in r for r in block.reasons)


def test_evaluate_reports_side_effects_and_irreversibility() -> None:
    registry = GovernanceRegistry.from_mapping({"d": {"risk_class": "destructive", "side_effects": ["delete"], "reversibility": "irreversible"}})
    decision = registry.evaluate("d")
    assert any("side_effects=delete" in r for r in decision.reasons)
    assert any("reversibility=irreversible" in r for r in decision.reasons)


def test_evaluate_untrusted_source_names_the_elevation() -> None:
    registry = GovernanceRegistry()
    decision = registry.evaluate("mcp_tool", untrusted_source=True)
    assert decision.action is ConfirmationPolicy.ASK
    assert any("provenance=elevated:untrusted_source" in r for r in decision.reasons)


def test_elevated_entry_above_write_can_never_be_auto() -> None:
    # Defensive escalation: an inferred entry for a class above WRITE is
    # escalated to ASK even if something constructed it with confirmation=auto.
    registry = GovernanceRegistry(
        entries={
            "weird": ToolGovernance(
                name="weird",
                risk_class=RiskClass.EXECUTE,
                confirmation=ConfirmationPolicy.AUTO,
                provenance="elevated",
            )
        }
    )
    decision = registry.evaluate("weird")
    assert decision.action is ConfirmationPolicy.ASK
    assert any("escalated:elevated_entry_above_write" in r for r in decision.reasons)


def test_declared_destructive_auto_stays_auto_only_when_declared() -> None:
    # A first-party tool that *declares* destructive+auto keeps its declared
    # policy: the declaration is explicit and reviewable, not inferred.
    registry = GovernanceRegistry()
    decision = registry.evaluate(
        "declared_tool",
        metadata={"governance_risk_class": "destructive", "governance_confirmation": "auto"},
    )
    assert decision.action is ConfirmationPolicy.AUTO
    assert decision.governance.provenance == "declared"
