"""W-N3 registry wave: discovery-plane descriptors for the dynamic workflow.

Covers the five ``alpha.workflow.registry`` registries (list/describe/health)
plus the honesty invariants the wave commits to:

- every descriptor validates, carries an allowed ``evidence_kind``, and pins
  ``version=None`` (no source declares a version today — nothing is invented);
- ``availability="unavailable"`` always carries a non-empty ``reason``;
- no descriptor claims runtime health (``health`` stays ``unverified`` — W-N3
  executes nothing);
- broken sources fail closed: ``list()`` raises ``RegistryUnavailable`` with
  the real exception text instead of reading as "zero entries", and
  ``health()`` never raises;
- the ``workflow_registry`` catalog entry is the production import chain for
  the package (loader reference), resolving to ``WorkflowRegistry``.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

from alpha.capabilities.catalog import CAPABILITY_CATALOG
from alpha.config.extensions_config import ExtensionsConfig
from alpha.skills.types import Skill, SkillCategory
from alpha.tools.tools import BUILTIN_TOOLS
from alpha.workflow.registry import (
    REGISTRY_KINDS,
    RegistryUnavailable,
    WorkflowRegistry,
    get_workflow_registry,
)
from alpha.workflow.registry.base import CapabilityDescriptor, RegistryHealth

_ALLOWED_EVIDENCE: set[str] = {"measured", "simulated", "heuristic", "unverified"}
_ALLOWED_HEALTH: set[str] = {"unverified", "unavailable"}


def _assert_descriptor_invariants(descriptor: CapabilityDescriptor) -> None:
    """Shared honesty invariants every descriptor must satisfy."""
    assert descriptor.id, "descriptor id must be non-empty"
    assert descriptor.kind, "descriptor kind must be non-empty"
    assert descriptor.source, "descriptor source must be non-empty"
    assert descriptor.evidence_kind in _ALLOWED_EVIDENCE
    assert descriptor.health in _ALLOWED_HEALTH, (
        "W-N3 executes nothing, so no descriptor may claim runtime health: "
        f"{descriptor.id} health={descriptor.health!r}"
    )
    assert descriptor.version is None, (
        "no source declares a version today; W-N3 must not invent one: "
        f"{descriptor.id} version={descriptor.version!r}"
    )
    if descriptor.availability == "unavailable":
        assert descriptor.reason and descriptor.reason.strip(), (
            f"unavailable descriptor must carry an honest reason: {descriptor.id}"
        )


def test_facade_exposes_all_five_registry_kinds() -> None:
    facade = WorkflowRegistry()
    assert REGISTRY_KINDS == ("capabilities", "tools", "skills", "mcp", "memory")
    for kind in REGISTRY_KINDS:
        registry = facade.registry(kind)
        assert registry.name == kind
        assert callable(registry.list) and callable(registry.describe) and callable(registry.health)
    singleton = get_workflow_registry()
    assert get_workflow_registry() is singleton, "singleton must be stable within a process"


def test_facade_unknown_kind_fails_closed() -> None:
    facade = WorkflowRegistry()
    with pytest.raises(KeyError, match="unknown registry kind"):
        facade.registry("bogus_registry")
    with pytest.raises(KeyError):
        facade.list("bogus_registry")


def test_capabilities_registry_matches_catalog() -> None:
    from alpha.workflow.registry.capabilities import CapabilityCatalogRegistry

    registry = CapabilityCatalogRegistry()
    descriptors = registry.list()
    assert len(descriptors) == len(CAPABILITY_CATALOG)
    assert {d.id for d in descriptors} == set(CAPABILITY_CATALOG)
    for descriptor in descriptors:
        _assert_descriptor_invariants(descriptor)
        assert descriptor.source == CAPABILITY_CATALOG[descriptor.id].module
        assert descriptor.kind == CAPABILITY_CATALOG[descriptor.id].kind

    known = registry.describe("dynamic_workflow_engine")
    assert known is not None
    assert known.source == "alpha.workflow.runtime"
    assert registry.describe("no_such_capability_id") is None

    health = registry.health()
    assert health.status == "ok"
    assert health.count == len(CAPABILITY_CATALOG)
    assert health.evidence_kind == "measured"


def test_capabilities_probe_failure_reports_honest_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alpha.workflow.registry.capabilities import CapabilityCatalogRegistry

    real_find_spec = importlib.util.find_spec

    def _broken(module: str, *args: object, **kwargs: object):
        if module == "alpha.bots.teammate_mesh":
            raise RuntimeError("probe exploded")
        return real_find_spec(module, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _broken)
    descriptor = CapabilityCatalogRegistry().describe("teammate_mesh")
    assert descriptor is not None
    assert descriptor.availability == "unavailable"
    assert descriptor.reason is not None
    assert "RuntimeError: probe exploded" in descriptor.reason
    _assert_descriptor_invariants(descriptor)


def test_builtin_tool_registry_matches_production_list() -> None:
    from alpha.workflow.registry.tools import BuiltinToolRegistry

    registry = BuiltinToolRegistry()
    descriptors = registry.list()
    assert len(BUILTIN_TOOLS) > 0, "production builtin tool list must not be empty"
    assert len(descriptors) == len(BUILTIN_TOOLS)
    assert {d.id for d in descriptors} == {tool.name for tool in BUILTIN_TOOLS}
    for descriptor in descriptors:
        _assert_descriptor_invariants(descriptor)
        assert descriptor.kind == "tool"
        assert descriptor.availability == "available"
        assert descriptor.source == "alpha.tools.tools:BUILTIN_TOOLS"

    first_name = BUILTIN_TOOLS[0].name
    known = registry.describe(first_name)
    assert known is not None
    assert known.id == first_name
    assert registry.describe("no_such_tool_xyz") is None

    health = registry.health()
    assert health.status == "ok"
    assert health.count == len(descriptors)


def test_skill_registry_maps_enabled_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from alpha.workflow.registry import skills as skills_module

    enabled_skill = Skill(
        name="enabled-skill",
        description="fixture skill that is enabled",
        license=None,
        skill_dir=Path("/skills/enabled-skill"),
        skill_file=Path("/skills/enabled-skill/SKILL.md"),
        relative_path=Path("enabled-skill"),
        category=SkillCategory.CUSTOM,
        enabled=True,
    )
    disabled_skill = Skill(
        name="disabled-skill",
        description="fixture skill that is disabled",
        license=None,
        skill_dir=Path("/skills/disabled-skill"),
        skill_file=Path("/skills/disabled-skill/SKILL.md"),
        relative_path=Path("disabled-skill"),
        category=SkillCategory.CUSTOM,
        enabled=False,
    )

    class _FakeStorage:
        def load_skills(self, *, enabled_only: bool = False) -> list[Skill]:
            return [enabled_skill, disabled_skill]

    monkeypatch.setattr(
        "alpha.skills.storage.get_or_new_skill_storage",
        lambda **kwargs: _FakeStorage(),
    )

    registry = skills_module.SkillRegistry()
    descriptors = registry.list()
    assert [d.id for d in descriptors] == ["enabled-skill", "disabled-skill"]
    for descriptor in descriptors:
        _assert_descriptor_invariants(descriptor)
        assert descriptor.kind == "skill"
        assert descriptor.evidence_kind == "measured"
        assert descriptor.version is None

    by_id = {d.id: d for d in descriptors}
    assert by_id["enabled-skill"].availability == "available"
    assert by_id["enabled-skill"].reason is None
    assert by_id["enabled-skill"].source == str(Path("/skills/enabled-skill"))
    assert by_id["disabled-skill"].availability == "unavailable"
    assert by_id["disabled-skill"].reason is not None
    assert "disabled" in by_id["disabled-skill"].reason

    assert registry.describe("enabled-skill") is not None
    assert registry.describe("no-such-skill") is None

    health = registry.health()
    assert health.status == "ok"
    assert health.count == 2


def test_skill_registry_broken_source_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from alpha.workflow.registry import skills as skills_module

    def _explode(**kwargs: object):
        raise OSError("skills root unreadable")

    monkeypatch.setattr("alpha.skills.storage.get_or_new_skill_storage", _explode)

    registry = skills_module.SkillRegistry()
    with pytest.raises(RegistryUnavailable) as excinfo:
        registry.list()
    assert "OSError: skills root unreadable" in str(excinfo.value)

    health = registry.health()
    assert health.status == "unavailable"
    assert health.count is None
    assert health.error is not None
    assert "OSError: skills root unreadable" in health.error
    assert health.evidence_kind == "measured"


def test_mcp_registry_matches_extensions_config() -> None:
    from alpha.workflow.registry.mcp import MCPServerRegistry

    config = ExtensionsConfig.from_file()
    registry = MCPServerRegistry()
    descriptors = registry.list()
    assert {d.id for d in descriptors} == set(config.mcp_servers)
    for descriptor in descriptors:
        _assert_descriptor_invariants(descriptor)
        assert descriptor.kind == "mcp_server"
        assert descriptor.evidence_kind == "measured"
        # Config is not connection evidence: the registry never probes a transport.
        assert descriptor.health == "unverified"
        server = config.mcp_servers[descriptor.id]
        if server.enabled:
            assert descriptor.availability == "available"
            assert descriptor.reason is None
        else:
            assert descriptor.availability == "unavailable"
            assert descriptor.reason is not None
            assert "disabled" in descriptor.reason

    if "github" in config.mcp_servers:
        known = registry.describe("github")
        assert known is not None
        assert known.source.endswith("#mcpServers.github")
    assert registry.describe("no_such_mcp_server") is None

    health = registry.health()
    assert health.status == "ok"
    assert health.count == len(config.mcp_servers)


def test_mcp_registry_broken_config_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from alpha.workflow.registry.mcp import MCPServerRegistry

    def _broken_from_file(cls: type) -> object:
        raise ValueError("extensions config is not valid JSON:boom")

    monkeypatch.setattr(ExtensionsConfig, "from_file", classmethod(_broken_from_file))

    registry = MCPServerRegistry()
    with pytest.raises(RegistryUnavailable) as excinfo:
        registry.list()
    assert "ValueError: extensions config is not valid JSON:boom" in str(excinfo.value)

    health = registry.health()
    assert health.status == "unavailable"
    assert health.error is not None
    assert "ValueError" in health.error


def test_memory_registry_probes_are_honest_and_isolated() -> None:
    from alpha.workflow.registry.memory import MEMORY_SUBSYSTEMS, MemoryRegistry

    registry = MemoryRegistry()
    descriptors = registry.list()
    assert [d.id for d in descriptors] == [entry_id for entry_id, _ in MEMORY_SUBSYSTEMS]
    assert [d.source for d in descriptors] == [module for _, module in MEMORY_SUBSYSTEMS]
    for descriptor in descriptors:
        _assert_descriptor_invariants(descriptor)
        assert descriptor.kind == "memory"
        # Probes measure importability, never a running store.
        assert descriptor.health == "unverified"
        if descriptor.availability == "available":
            assert descriptor.evidence_kind == "measured"
            assert descriptor.reason is None

    assert registry.describe("cognitive") is not None
    assert registry.describe("no_such_tier") is None

    health = registry.health()
    assert health.status == "ok"
    assert health.count == len(MEMORY_SUBSYSTEMS)
    assert health.evidence_kind == "measured"


def test_memory_registry_probe_failure_carries_real_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alpha.workflow.registry.memory import MemoryRegistry

    def _broken(module: str, *args: object, **kwargs: object):
        raise RuntimeError("spec lookup exploded")

    monkeypatch.setattr(importlib.util, "find_spec", _broken)
    descriptors = MemoryRegistry().list()
    assert len(descriptors) > 0
    for descriptor in descriptors:
        assert descriptor.availability == "unavailable"
        assert descriptor.reason is not None
        assert "RuntimeError: spec lookup exploded" in descriptor.reason
        _assert_descriptor_invariants(descriptor)


def test_health_isolation_one_broken_registry_never_hides_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alpha.workflow.registry.capabilities import CapabilityCatalogRegistry

    def _broken_list(self: CapabilityCatalogRegistry) -> list[CapabilityDescriptor]:
        raise RuntimeError("catalog gone")

    monkeypatch.setattr(CapabilityCatalogRegistry, "list", _broken_list)

    report = WorkflowRegistry().health()
    assert set(report) == set(REGISTRY_KINDS)
    for kind, health in report.items():
        assert isinstance(health, RegistryHealth)
        assert health.registry == kind
    assert report["capabilities"].status == "unavailable"
    assert report["capabilities"].error is not None
    assert "RuntimeError: catalog gone" in report["capabilities"].error
    for kind in ("tools", "skills", "mcp", "memory"):
        assert report[kind].status == "ok", f"{kind} must stay ok when capabilities breaks"
        assert report[kind].count is not None


def test_live_descriptors_make_no_running_subsystem_claims() -> None:
    """Cross-registry honesty sweep over whatever is really on this machine."""
    facade = WorkflowRegistry()
    checked = 0
    for kind in REGISTRY_KINDS:
        try:
            descriptors = facade.list(kind)
        except RegistryUnavailable as exc:
            # Fail-closed IS the honest outcome; the error must carry real text.
            assert str(exc)
            continue
        for descriptor in descriptors:
            _assert_descriptor_invariants(descriptor)
            assert descriptor.health == "unverified", (
                f"{kind}:{descriptor.id} claims runtime health without a runtime probe"
            )
            checked += 1
    assert checked > 0, "at least one registry must have produced descriptors"


def test_catalog_entry_is_the_production_import_chain() -> None:
    """The workflow_registry catalog entry must resolve to a real target."""
    spec = CAPABILITY_CATALOG.get("workflow_registry")
    assert spec is not None, "workflow_registry capability entry missing from catalog"
    assert spec.module == "alpha.workflow.registry"
    assert spec.target == "WorkflowRegistry"

    module = importlib.import_module(spec.module)
    target = getattr(module, spec.target)
    assert target is WorkflowRegistry
    assert spec.dotted_target == "alpha.workflow.registry:WorkflowRegistry"

    instance = target()
    assert set(instance.health()) == set(REGISTRY_KINDS)
