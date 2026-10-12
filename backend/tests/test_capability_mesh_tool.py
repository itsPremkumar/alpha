"""Tests for the model-facing ``capability_mesh`` tool.

Every action, offline, against a ``tmp_path`` mesh directory, with zero model
calls. The properties being pinned are the honesty ones rather than the
mechanical ones:

* ``route``/``team``/``compose``/``explain`` are PREVIEWS computed locally and
  say so — nothing was dispatched;
* a count is reported beside the evidence level behind it, and unmeasured is
  captioned unmeasured;
* a corrupt store surfaces the store's own refusal reason instead of an
  empty answer, while a *missing* file is genuinely empty;
* the single write (``record``) goes through the mesh's own validation, so a
  score without its profile version is refused rather than stored;
* no store I/O or config read runs on the event loop.

The backing service is ``alpha.capability_mesh.service.CapabilityMeshService``,
which the HTTP router calls too — so a rule proved here holds on both surfaces.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.prebuilt import ToolRuntime

from alpha.capability_mesh.config import CapabilityMeshConfig, capability_mesh_config
from alpha.capability_mesh.memory import CapabilityMemory, CapabilityRecord, OutcomeKind
from alpha.capability_mesh.models import CapabilityEstimate, CapabilityPassport, EntityKind, EvidenceLevel
from alpha.capability_mesh.store import PassportStore
from alpha.tools.builtins.capability_mesh_tool import capability_mesh_tool

# Async tests are marked rather than auto-detected: this repo runs pytest-asyncio
# in strict mode, so an unmarked ``async def`` is skipped with an error.
pytestmark = pytest.mark.asyncio

PV = "v1"

#: A task the pinned keyword baseline actually fires on: "debug" is
#: complexity_high, "production" is risk_critical and "urgent" is
#: pressure_high. A task matching nothing reads Band.UNKNOWN, which would make
#: a routing assertion vacuous.
TASK = "Debug the urgent production latency regression before the release."

ALL_ACTIONS = ("passports", "memory", "calibration", "drift", "policy", "route", "team", "compose", "explain", "independence", "shadow", "record")


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def mesh_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "capability_mesh"
    directory.mkdir()
    return directory


def _runtime() -> ToolRuntime:
    """A minimal ``ToolRuntime``; ``runtime`` is an injected argument."""
    return ToolRuntime(state={}, context={}, config={}, stream_writer=lambda _: None, tool_call_id="tool-call-1", store=None)


def _tool_module():
    """The tool *module*, not the bound ``StructuredTool``.

    ``from ... import capability_mesh_tool`` yields the tool object (the package
    attribute shadows the submodule), so patching ``get_app_config`` through it
    would set an attribute on a pydantic model instead of on the module.
    """
    import importlib

    return importlib.import_module("alpha.tools.builtins.capability_mesh_tool")


def _install_config(monkeypatch, directory: Path, **overrides) -> CapabilityMeshConfig:
    """Point the tool at a temp mesh directory, as arena_tool resolves config."""
    config = CapabilityMeshConfig(directory=str(directory), **{"enabled": True, **overrides})
    module = _tool_module()
    monkeypatch.setattr(module, "get_app_config", lambda: SimpleNamespace(capability_mesh=config))
    monkeypatch.setattr(module, "capability_mesh_config", lambda app_config=None: config)
    return config


def _seed_passport(directory: Path, *, entity_id: str = "agent:planner", capabilities: tuple[str, ...] = ("debug",)) -> None:
    """Write one passport. Unmeasured by default: the mesh never invents a score."""
    estimates = tuple(CapabilityEstimate(capability=name, task_category="*", sample_count=0, evidence_level=EvidenceLevel.UNMEASURED) for name in capabilities)
    PassportStore(directory).upsert(CapabilityPassport(entity_id=entity_id, entity_type=EntityKind.AGENT, version="v1", capabilities=estimates))


def _seed_measured_passport(directory: Path, *, entity_id: str = "agent:planner") -> None:
    PassportStore(directory).upsert(
        CapabilityPassport(
            entity_id=entity_id,
            entity_type=EntityKind.AGENT,
            version="v1",
            capabilities=(CapabilityEstimate(capability="debug", task_category="*", quality_mean=0.8, uncertainty=0.05, sample_count=12, evidence_level=EvidenceLevel.BENCHMARKED, profile_version=PV),),
        )
    )


async def _call(**kwargs) -> str:
    return await capability_mesh_tool.coroutine(runtime=_runtime(), **kwargs)


# ------------------------------------------------------------------ config gate


class TestConfigGate:
    async def test_disabled_mesh_refuses_by_name(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir, enabled=False)
        text = await _call(action="passports")
        assert "disabled" in text
        assert "capability_mesh.enabled" in text

    def test_no_config_loaded_yields_the_inert_default(self) -> None:
        """Absent config => defaults => ``enabled: False``. The safe direction."""
        assert capability_mesh_config(None).enabled is False
        assert capability_mesh_config(SimpleNamespace()).enabled is False

    def test_directory_defaults_under_the_runtime_home(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ALPHA_HOME", str(tmp_path / "home"))
        assert CapabilityMeshConfig().resolved_directory() == (tmp_path / "home").resolve() / "capability_mesh"

    def test_an_explicit_directory_wins(self, tmp_path: Path) -> None:
        assert CapabilityMeshConfig(directory=str(tmp_path / "mesh")).resolved_directory() == tmp_path / "mesh"

    def test_config_defaults_are_the_safe_ones(self) -> None:
        config = CapabilityMeshConfig()
        assert config.enabled is False
        assert config.default_profile_version == "v1"
        assert config.max_records >= 1
        assert 0.0 <= config.exploration_budget_fraction <= 1.0

    def test_bounds_are_refused_not_clamped(self) -> None:
        with pytest.raises(ValueError):
            CapabilityMeshConfig(max_records=0)
        with pytest.raises(ValueError):
            CapabilityMeshConfig(exploration_budget_fraction=1.5)

    def test_an_empty_default_profile_version_loads_and_is_caught_at_the_record(self, mesh_dir: Path) -> None:
        """The misconfiguration is not fatal at load; it is refused where it bites.

        Refusing it in the schema would be a startup failure for an operator who
        never records anything; the rule that matters is "no score without its
        profile version", and that is enforced by the record type.
        """
        assert CapabilityMeshConfig(default_profile_version="").default_profile_version == ""
        from alpha.capability_mesh.service import CapabilityMeshService

        service = CapabilityMeshService(CapabilityMeshConfig(enabled=True, directory=str(mesh_dir), default_profile_version=""))
        with pytest.raises(ValueError, match="profile_version"):
            service.record(entity_id="agent:planner", capability="debug", outcome="selected", task_fingerprint="fp_1", quality=0.8)

    def test_a_misspelled_key_is_refused(self) -> None:
        with pytest.raises(ValueError):
            CapabilityMeshConfig(memory_records=10)


# ------------------------------------------------------------------- read actions


class TestReadActions:
    async def test_passports_reports_a_count_with_its_evidence_level(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir)
        text = await _call(action="passports")
        assert "agent:planner" in text
        assert "unmeasured" in text

    async def test_passports_on_an_empty_store_is_empty_not_broken(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        text = await _call(action="passports")
        assert "no passports recorded" in text.lower()
        assert "store error" not in text.lower()

    async def test_passports_reports_the_measured_level_when_there_is_one(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_measured_passport(mesh_dir)
        assert "benchmarked" in await _call(action="passports")

    async def test_passports_filters_by_entity(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir, entity_id="agent:planner")
        _seed_passport(mesh_dir, entity_id="agent:other")
        assert "agent:other" not in await _call(action="passports", entity_id="agent:planner")

    async def test_memory_lists_records_and_credit(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_record(mesh_dir, record_id="r1", entity_id="agent:planner")
        text = await _call(action="memory", entity_id="agent:planner")
        assert "r1" in text
        assert "credit" in text.lower()

    async def test_memory_on_an_empty_store_says_so(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        text = await _call(action="memory")
        assert "0 record(s)" in text
        assert "store error" not in text.lower()

    async def test_calibration_says_unresolved_over_nothing(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        text = await _call(action="calibration")
        assert "unresolved" in text.lower()

    async def test_calibration_is_scoped_to_one_profile_version(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_record(mesh_dir, record_id="r1", quality=0.8, confidence=0.9)
        assert "v2" in await _call(action="calibration", profile_version="v2")

    async def test_drift_reports_and_does_not_act(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        text = await _call(action="drift")
        assert "drift" in text.lower()
        assert "acts on nothing" in text.lower()

    async def test_policy_lists_the_resolution_table(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        text = await _call(action="policy", policy="research_grade")
        assert "research_grade" in text
        assert "balanced" in text

    async def test_policy_reports_the_declared_exploration_share_without_spending_it(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir, exploration_budget_fraction=0.25)
        text = await _call(action="policy")
        assert "0.25" in text
        assert "not spent" in text.lower()

    async def test_policy_refuses_an_unknown_name(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        assert "unknown policy" in (await _call(action="policy", policy="nope")).lower()

    async def test_shadow_reports_no_rate_over_nothing(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        text = await _call(action="shadow")
        assert "shadow" in text.lower()
        assert "none" in text.lower()

    async def test_independence_needs_two_distinct_entities(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir)
        _seed_passport(mesh_dir, entity_id="agent:other")
        text = await _call(action="independence", entity_a="agent:planner", entity_b="agent:planner")
        assert "distinct" in text.lower()

    async def test_independence_reports_unknown_as_unknown(self, monkeypatch, mesh_dir: Path) -> None:
        """Nothing measured is neither independent nor dependent."""
        _install_config(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir)
        _seed_passport(mesh_dir, entity_id="agent:other")
        text = await _call(action="independence", entity_a="agent:planner", entity_b="agent:other")
        assert "unknown" in text.lower() or "known=false" in text.lower()

    async def test_independence_refuses_a_missing_operand(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        assert "entity_a" in await _call(action="independence", entity_a="agent:planner")


# -------------------------------------------------------------- preview actions


class TestPreviewActions:
    @pytest.mark.parametrize("action", ["route", "team", "compose", "explain"])
    async def test_preview_actions_are_labelled_and_dispatch_nothing(self, monkeypatch, mesh_dir: Path, action: str) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_measured_passport(mesh_dir)
        text = await _call(action=action, task=TASK, required_capabilities="debug")
        assert "PREVIEW" in text, action
        assert "nothing was dispatched" in text.lower(), action

    @pytest.mark.parametrize("action", ["route", "team", "compose", "explain"])
    async def test_preview_actions_require_task_text(self, monkeypatch, mesh_dir: Path, action: str) -> None:
        _install_config(monkeypatch, mesh_dir)
        assert "requires" in (await _call(action=action)).lower(), action

    async def test_route_names_the_heuristic_decomposer(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_measured_passport(mesh_dir)
        assert "heuristic_baseline" in await _call(action="route", task=TASK, required_capabilities="debug")

    async def test_route_refuses_every_microtask_when_no_passport_exists(self, monkeypatch, mesh_dir: Path) -> None:
        """No eligible entity is a refusal row, never a silent drop."""
        _install_config(monkeypatch, mesh_dir)
        text = await _call(action="route", task=TASK, required_capabilities="debug")
        assert "no eligible entity" in text.lower()

    async def test_route_refuses_an_out_of_bounds_policy_budget(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_measured_passport(mesh_dir)
        assert "default_agents" in await _call(action="route", task=TASK, required_capabilities="debug", default_agents=999)

    async def test_team_reports_its_stop_reason(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_measured_passport(mesh_dir)
        text = await _call(action="team", task=TASK, required_capabilities="debug")
        assert "stopped because" in text.lower()
        assert "independence" in text.lower()

    async def test_compose_accepts_supplied_artifacts(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_measured_passport(mesh_dir)
        text = await _call(action="compose", task=TASK, required_capabilities="debug", artifacts_json=json.dumps([_artifact()]))
        assert "composition" in text.lower() or "composed" in text.lower()

    async def test_compose_persists_no_artifact(self, monkeypatch, mesh_dir: Path) -> None:
        """The mesh's one write is ``record``; a preview leaves no artifact behind."""
        _install_config(monkeypatch, mesh_dir)
        _seed_measured_passport(mesh_dir)
        await _call(action="compose", task=TASK, required_capabilities="debug", artifacts_json=json.dumps([_artifact()]))
        assert not (mesh_dir / "artifacts.json").exists()

    async def test_compose_refuses_a_malformed_artifact_payload(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        assert "artifacts_json" in (await _call(action="compose", task=TASK, artifacts_json="{not json")).lower()

    async def test_explain_captions_unmeasured_as_unmeasured(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_measured_passport(mesh_dir)
        assert "unmeasured" in (await _call(action="explain", task=TASK, required_capabilities="debug")).lower()


# ------------------------------------------------------------------ write action


class TestWriteAction:
    async def test_record_appends_one_validated_row(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        text = await _call(action="record", entity_id="agent:planner", capability="debug", outcome="selected", task_fingerprint="fp_1", quality=0.8, profile_version=PV)
        assert "recorded" in text.lower()
        assert [record.record_id for record in CapabilityMemory(mesh_dir).records()]

    async def test_record_never_stores_raw_task_text(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        await _call(action="record", entity_id="agent:planner", capability="debug", outcome="selected", task="a very secret prompt body")
        stored = CapabilityMemory(mesh_dir).records()[0].to_dict()
        assert stored["task_fingerprint"].startswith("fp_")
        assert "secret" not in json.dumps(stored)

    async def test_record_refuses_a_quality_without_a_profile_version(self, monkeypatch, mesh_dir: Path) -> None:
        """The mesh's own constructor refuses it; the tool lets it."""
        _install_config(monkeypatch, mesh_dir, default_profile_version="")
        text = await _call(action="record", entity_id="agent:planner", capability="debug", outcome="selected", task_fingerprint="fp_1", quality=0.8, profile_version="")
        assert "profile_version" in text
        assert CapabilityMemory(mesh_dir).records() == []

    async def test_record_refuses_an_unknown_outcome(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        assert "unknown outcome" in (await _call(action="record", entity_id="agent:planner", capability="debug", outcome="triumphed", task_fingerprint="fp_1")).lower()

    async def test_record_requires_a_task_fingerprint_or_text(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        assert "task_fingerprint" in await _call(action="record", entity_id="agent:planner", capability="debug", outcome="selected")

    async def test_record_rejects_a_truthy_string_hard_gate(self, monkeypatch, mesh_dir: Path) -> None:
        """``passed_hard_gate="yes"`` must never become a silent pass."""
        _install_config(monkeypatch, mesh_dir)
        assert "passed_hard_gate" in await _call(action="record", entity_id="agent:planner", capability="debug", outcome="selected", task_fingerprint="fp_1", passed_hard_gate="yes")

    async def test_record_rejects_an_out_of_unit_quality(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        assert "quality" in await _call(action="record", entity_id="agent:planner", capability="debug", outcome="selected", task_fingerprint="fp_1", quality=1.4, profile_version=PV)

    async def test_record_refuses_a_duplicate_record_id(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        await _call(action="record", entity_id="agent:planner", capability="debug", outcome="selected", task_fingerprint="fp_1", profile_version=PV, record_id="r1")
        assert "duplicate" in (await _call(action="record", entity_id="agent:planner", capability="debug", outcome="selected", task_fingerprint="fp_1", profile_version=PV, record_id="r1")).lower()


# ---------------------------------------------------------------- corrupt stores


class TestCorruptStore:
    async def test_corrupt_passport_store_surfaces_the_real_reason(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        (mesh_dir / "passports.json").write_text("{ this is not json", encoding="utf-8")
        text = await _call(action="passports")
        assert "store error" in text.lower()
        assert "passports.json" in text
        assert "no passports recorded" not in text.lower()

    async def test_corrupt_memory_surfaces_the_real_reason(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        (mesh_dir / "capability_memory.jsonl").write_text("{ not json\n", encoding="utf-8")
        text = await _call(action="memory")
        assert "store error" in text.lower()
        assert "capability_memory.jsonl" in text

    async def test_corrupt_shadow_store_surfaces_the_real_reason(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        (mesh_dir / "shadow.json").write_text("{ not json", encoding="utf-8")
        text = await _call(action="shadow")
        assert "store error" in text.lower()
        assert "shadow.json" in text

    async def test_a_dishonest_stored_record_is_refused_by_name(self, monkeypatch, mesh_dir: Path) -> None:
        """A hand-edited row carrying a score with no profile version cannot load."""
        _install_config(monkeypatch, mesh_dir)
        (mesh_dir / "capability_memory.jsonl").write_text(
            json.dumps(
                {
                    "record_id": "r1",
                    "task_fingerprint": "fp_1",
                    "task_category": "general",
                    "entity_id": "agent:planner",
                    "capability": "debug",
                    "outcome": "selected",
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "quality": 0.9,
                    "profile_version": "",
                    "confidence": None,
                    "slot": None,
                    "passed_hard_gate": None,
                    "latency_ms": None,
                    "cost_usd": None,
                    "failure_mode": None,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        text = await _call(action="memory")
        assert "store error" in text.lower()
        assert "profile_version" in text

    async def test_a_missing_file_is_empty_not_broken(self, monkeypatch, mesh_dir: Path) -> None:
        """Absence is not corruption — the two must stay distinguishable."""
        _install_config(monkeypatch, mesh_dir)
        text = await _call(action="memory")
        assert "store error" not in text.lower()


# ---------------------------------------------------------------- event loop I/O


class TestNoBlockingIo:
    """Run on a plain (non-asyncio) test so the loop thread is this thread.

    ``threading.get_ident()`` captured here is the thread that will own the
    event loop, so a store read made inline by the tool is caught by name.
    """

    def test_store_reads_do_not_run_on_the_event_loop(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_measured_passport(mesh_dir)
        _seed_record(mesh_dir, record_id="r1", quality=0.8)
        offenders: list[str] = []
        _wrap_path_open(monkeypatch, offenders)

        async def _run() -> None:
            for action in ("passports", "memory", "calibration", "drift", "policy", "shadow"):
                await _call(action=action)

        asyncio.run(_run())
        assert offenders == [], f"store I/O ran on the event loop: {offenders}"

    def test_config_resolution_does_not_run_on_the_event_loop(self, monkeypatch, mesh_dir: Path) -> None:
        """``get_app_config`` stats the config file, so it is never read inline.

        ``runtime.app_config`` is preferred when the runtime carries one; the
        fallback that actually touches disk runs through ``asyncio.to_thread``.
        """
        _install_config(monkeypatch, mesh_dir)
        offenders: list[str] = []
        _wrap_path_open(monkeypatch, offenders)
        asyncio.run(_call(action="passports"))
        assert offenders == [], f"config resolution ran on the event loop: {offenders}"

    def test_preview_computation_does_not_run_on_the_event_loop(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        _seed_measured_passport(mesh_dir)
        offenders: list[str] = []
        _wrap_path_open(monkeypatch, offenders)

        async def _run() -> None:
            for action in ("route", "team", "compose", "explain"):
                await _call(action=action, task=TASK, required_capabilities="debug")

        asyncio.run(_run())
        assert offenders == [], f"preview store I/O ran on the event loop: {offenders}"


def _wrap_path_open(monkeypatch, offenders: list[str]) -> None:
    """Record every ``Path.open`` made from the thread that owns the loop."""
    loop_thread = threading.get_ident()
    real_open = Path.open

    def _tracking_open(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if threading.get_ident() == loop_thread:
            offenders.append(str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _tracking_open)


# ---------------------------------------------------------------- unknown action


class TestUnknownAction:
    async def test_unknown_action_is_refused_by_name(self, monkeypatch, mesh_dir: Path) -> None:
        _install_config(monkeypatch, mesh_dir)
        assert "unknown capability mesh action" in (await _call(action="teleport")).lower()


# ------------------------------------------------------------------ tool wiring


class TestToolWiring:
    def test_tool_is_named_capability_mesh(self) -> None:
        assert capability_mesh_tool.name == "capability_mesh"

    def test_tool_schema_generates(self) -> None:
        schema = capability_mesh_tool.tool_call_schema.model_json_schema()
        assert "action" in schema["properties"]
        assert "artifacts_json" in schema["properties"], "compose takes its artifacts as an explicit JSON payload"

    def test_every_action_is_declared(self) -> None:
        properties = capability_mesh_tool.tool_call_schema.model_json_schema()["properties"]
        values = properties["action"].get("enum") or [entry["const"] for entry in properties["action"].get("anyOf", []) if "const" in entry]
        assert set(values) == set(ALL_ACTIONS)

    def test_tool_is_registered_in_builtin_tools(self) -> None:
        from alpha.tools.tools import BUILTIN_TOOLS

        assert any(getattr(tool, "name", None) == "capability_mesh" for tool in BUILTIN_TOOLS)

    def test_tool_is_exported_from_builtins(self) -> None:
        from alpha.tools import builtins

        assert "capability_mesh_tool" in builtins.__all__
        assert builtins.capability_mesh_tool.name == "capability_mesh"


# ------------------------------------------------------------- service contract


class TestServiceContract:
    def test_reads_are_read_only_until_record(self, mesh_dir: Path) -> None:
        from alpha.capability_mesh.service import CapabilityMeshService

        service = CapabilityMeshService(CapabilityMeshConfig(enabled=True, directory=str(mesh_dir)))
        _seed_passport(mesh_dir)
        before = sorted(path.name for path in mesh_dir.iterdir())
        service.passports()
        service.memory_report()
        service.calibration_report()
        service.drift_report()
        service.policy_table()
        service.shadow_report()
        assert sorted(path.name for path in mesh_dir.iterdir()) == before

    def test_record_is_the_only_write(self, mesh_dir: Path) -> None:
        from alpha.capability_mesh.service import CapabilityMeshService

        service = CapabilityMeshService(CapabilityMeshConfig(enabled=True, directory=str(mesh_dir)))
        assert not (mesh_dir / "capability_memory.jsonl").exists()
        service.record(entity_id="agent:planner", capability="debug", outcome="selected", task_fingerprint="fp_1", quality=0.8, profile_version=PV)
        assert (mesh_dir / "capability_memory.jsonl").exists()

    def test_a_corrupt_store_raises_with_the_store_s_own_reason(self, mesh_dir: Path) -> None:
        from alpha.capability_mesh.service import CapabilityMeshService
        from alpha.capability_mesh.store import PassportStoreError

        (mesh_dir / "passports.json").write_text("{ corrupt", encoding="utf-8")
        service = CapabilityMeshService(CapabilityMeshConfig(enabled=True, directory=str(mesh_dir)))
        with pytest.raises(PassportStoreError) as excinfo:
            service.passports()
        assert "unreadable" in str(excinfo.value).lower()

    def test_preview_payloads_carry_the_preview_and_evidence_contract(self, mesh_dir: Path) -> None:
        from alpha.capability_mesh.service import CapabilityMeshService

        service = CapabilityMeshService(CapabilityMeshConfig(enabled=True, directory=str(mesh_dir)))
        _seed_measured_passport(mesh_dir)
        for payload in (
            service.route_preview(task=TASK, required_capabilities=("debug",)),
            service.team_preview(task=TASK, required_capabilities=("debug",)),
            service.compose_preview(task=TASK, artifacts=[_artifact()], required_capabilities=("debug",)),
            service.explain_preview(task=TASK, required_capabilities=("debug",)),
        ):
            assert payload["preview"] is True
            assert payload["dispatched"] is False
            assert any("unmeasured" in note.lower() for note in payload["notes"])


# ------------------------------------------------------------------- helpers


def _artifact() -> dict:
    return {
        "artifact_id": "a1",
        "entity_id": "agent:planner",
        "microtask_id": "mt-1",
        "content": "a bounded fix for the regression",
        "mime_type": "text/plain",
        "capability_coverage": ["debug"],
        "evidence_level": "observed",
        "quality": 0.9,
        "passed_hard_gate": True,
        "recorded_at": datetime.now(UTC).isoformat(),
    }


def _seed_record(directory: Path, *, record_id: str, entity_id: str = "agent:planner", quality: float | None = None, confidence: float | None = None) -> None:
    CapabilityMemory(directory).record(
        CapabilityRecord(
            record_id=record_id,
            task_fingerprint=f"fp_{record_id}",
            task_category="general",
            entity_id=entity_id,
            capability="debug",
            outcome=OutcomeKind.SELECTED,
            recorded_at=datetime.now(UTC).isoformat(),
            quality=quality,
            confidence=confidence,
            profile_version=PV,
        )
    )
