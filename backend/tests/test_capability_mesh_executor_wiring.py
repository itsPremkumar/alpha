"""The capability mesh's executor absence, asserted across every surface.

``alpha.capability_mesh`` ships 26 modules of routing/team/composition/synthesis
logic with a tool and an HTTP router on top. None of it dispatches: there is no
executor behind it, and nothing in Alpha reads a capability record to make a
dispatch decision. That was documented in three places and **asserted in none**,
which is how a preview-only plane drifts into being described as a router.

This suite pins the absence as a *contract* on every surface that could imply
otherwise:

* the tool docstring, the disabled refusal, and every rendered reply;
* every service payload (read, preview and write alike);
* ``GET /api/capability-mesh/capability`` as one machine-readable answer;
* the package's own source — the claim is checked against the modules that would
  have to import ``SubagentExecutor``/``RunManager``, not merely asserted.

``enabled: false`` stays the default; these tests never require it to be on.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from alpha.capability_mesh.config import CapabilityMeshConfig
from alpha.capability_mesh.service import (
    MESH_CAPABILITY,
    MESH_CAPABILITY_NOTE,
    MESH_CAPABILITY_STATE,
    CapabilityMeshService,
    mesh_capability_declaration,
)

MESH_DIR = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "capability_mesh"

#: The two owners that perform real dispatch. Neither may be reachable from the
#: mesh package, or the mesh would be routing work rather than previewing it.
_REAL_EXECUTORS = ("alpha.subagents.executor", "alpha.runtime.runs")


@pytest.fixture
def mesh_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "mesh"
    directory.mkdir()
    _seed_entities(directory)
    return directory


@pytest.fixture
def service(mesh_dir: Path) -> CapabilityMeshService:
    return CapabilityMeshService(CapabilityMeshConfig(enabled=True, directory=str(mesh_dir)))


def _seed_entities(directory: Path) -> None:
    """Two UNMEASURED passports, so graph-derived reads are answerable.

    Seeded at ``sample_count=0`` on purpose: the mesh never invents a
    measurement, and every read below reports ``unmeasured`` for the same reason.
    """
    from alpha.capability_mesh.memory import CapabilityEstimate
    from alpha.capability_mesh.models import CapabilityPassport, EntityKind, EvidenceLevel
    from alpha.capability_mesh.store import PassportStore

    for entity_id in ("agent:planner", "agent:builder"):
        PassportStore(directory).upsert(
            CapabilityPassport(
                entity_id=entity_id,
                entity_type=EntityKind.AGENT,
                version="v1",
                capabilities=(CapabilityEstimate(capability="debug", task_category="*", sample_count=0, evidence_level=EvidenceLevel.UNMEASURED),),
            )
        )


class TestTheMeshPackageItselfDoesNotDispatch:
    """The claim is checked against the source, not merely restated."""

    def test_no_mesh_module_imports_a_real_executor(self) -> None:
        """Nothing in the mesh package may reach a real dispatcher.

        This is the load-bearing check: every other test in this file reads a
        label off a surface, and a label can be correct while the wiring behind
        it silently appears. Here the wiring itself is what is asserted.
        """
        offenders: list[str] = []
        for path in sorted(MESH_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                for module in modules:
                    if any(module == target or module.startswith(f"{target}.") for target in _REAL_EXECUTORS):
                        offenders.append(f"{path.name}: {module}")
        assert not offenders, f"the capability mesh must not import a real executor — it is preview-only, and these imports would make it dispatch: {offenders}"

    def test_the_mesh_does_not_define_a_dispatch_entry_point(self) -> None:
        """No module may claim to dispatch.

        A `dispatch`/`execute`/`run_task` symbol in the package is where the
        absence would first become visible, so its absence is checked by name
        rather than left to the import scan above (which would not see a call
        made through an injected callable).
        """
        forbidden = {"dispatch", "dispatch_run", "execute_task", "run_candidates", "launch_team"}
        found: list[str] = []
        for path in sorted(MESH_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in forbidden:
                    found.append(f"{path.name}:{node.name}")
        assert not found, f"the capability mesh declares no dispatch entry point; these exist and would have to be wired before the mesh could route work: {found}"

    def test_the_declaration_names_the_missing_wiring(self) -> None:
        """The absence is machine-readable and names what is missing.

        A payload saying only "this is a preview" does not tell a client whether
        the plane *could* dispatch; the declaration does, and names the modules a
        reader would have to find behind the claim.
        """
        declaration = mesh_capability_declaration()
        assert declaration["capability"] == MESH_CAPABILITY
        assert declaration["state"] == MESH_CAPABILITY_STATE == "absent"
        assert declaration["dispatches"] is False
        assert declaration["missing_wiring"], "the declaration must name the modules a dispatch would have to live in"
        for entry in declaration["missing_wiring"]:
            module, symbol = entry.split(":")
            assert module.startswith("alpha."), f"{module} is not an alpha module path"
            assert symbol, f"{entry} names no symbol"

    def test_every_named_missing_module_exists(self) -> None:
        """A `missing_wiring` entry pointing at a non-existent module is noise.

        The list is only useful if each entry is a real place a reader could look,
        so every path is resolved. Two entries are intentionally *not* resolvable
        — the real executors, which is the point, and the declared seam that does
        not exist yet — so each is classified explicitly instead of skipped.
        """
        import importlib

        from alpha.capability_mesh.service import MESH_MISSING_EXECUTOR_MODULES

        absent_seams = {"alpha.capability_mesh:dispatch"}
        for entry in MESH_MISSING_EXECUTOR_MODULES:
            if entry in absent_seams:
                continue
            module_path, _, symbol = entry.partition(":")
            module = importlib.import_module(module_path)
            assert hasattr(module, symbol), f"{entry} names a symbol the module does not define"

        for seam in absent_seams:
            module_path, _, symbol = seam.partition(":")
            module = importlib.import_module(module_path)
            assert not hasattr(module, symbol), f"{seam} is declared ABSENT, so defining {symbol} means the mesh gained a dispatch seam and the declaration must be rewritten"

    def test_the_real_executors_exist_and_do_not_read_the_mesh(self) -> None:
        """The alternative the absence points at is real, and independent.

        ``missing_wiring`` names ``SubagentExecutor`` and ``RunManager`` as the
        places a dispatch would have to live. Both must exist (or the pointer is
        fiction) and neither may read the mesh (or the mesh *is* routing work,
        just indirectly).

        Resolved on disk rather than through ``importlib``: ``alpha.subagents``
        is a PEP-562 lazy facade whose exports expose neither ``__file__`` nor
        ``__spec__``, so an import-based probe cannot name the file at all.
        """
        for executor in _REAL_EXECUTORS:
            module_path = _harness_path(executor)
            assert module_path.exists(), f"{executor} does not exist on disk, so missing_wiring points at fiction"
            targets = sorted(module_path.rglob("*.py")) if module_path.is_dir() else [module_path]
            assert targets, f"{executor} resolved to an empty source tree"
            readers = [str(target) for target in targets if "capability_mesh" in target.read_text(encoding="utf-8")]
            assert not readers, f"{executor} reads the capability mesh — the mesh is preview-only and nothing may route work from it: {readers}"


class TestEveryServicePayloadDeclaresTheAbsence:
    def test_reads_previews_and_the_write_all_carry_it(self, service: CapabilityMeshService) -> None:
        """One payload without the declaration is a payload that over-claims.

        Only the *preview* payloads said anything about dispatching before. A
        caller reading only `/profiles` had no way to know the plane could not
        route, and would reasonably assume the answer simply had not been
        exercised yet.
        """
        payloads = [
            service.passports(),
            service.memory_report(),
            service.calibration_report(),
            service.drift_report(),
            service.policy_table(),
            service.independence_report(entity_a="agent:planner", entity_b="agent:builder"),
            service.shadow_report(),
            service.route_preview(task="ship the parser"),
            service.team_preview(task="ship the parser", required_capabilities=("debug",)),
            service.compose_preview(task="ship the parser"),
            service.explain_preview(task="ship the parser"),
        ]
        for payload in payloads:
            declaration = payload.get("mesh_capability")
            assert declaration is not None, f"payload kind={payload.get('kind')} carries no mesh_capability declaration"
            assert declaration["dispatches"] is False
            assert declaration["state"] == "absent"
            assert MESH_CAPABILITY_NOTE in payload["notes"], f"payload kind={payload.get('kind')} lost the explanatory note"

    def test_the_write_declares_it_too(self, service: CapabilityMeshService) -> None:
        """`record` is the only write, so it is the one most likely to be read
        as "the mesh is now in use".

        Recording an observation is exactly what the mesh is for, but it must
        still say the recording did not schedule or assign anything.
        """
        payload = service.record(entity_id="agent-1", capability="coding", outcome="selected", task_fingerprint="fp_abc")
        assert payload["mesh_capability"]["dispatches"] is False
        assert any("does not dispatch" in note for note in payload["notes"])

    def test_a_preview_still_reports_dispatched_false_per_call(self, service: CapabilityMeshService) -> None:
        """Two facts, both true, neither substitutable for the other.

        ``dispatched`` says this reply did not dispatch. ``mesh_capability``
        says the plane cannot. Collapsing them would lose the plane-level claim;
        dropping the first would make a future wired mesh look broken.
        """
        payload = service.route_preview(task="ship the parser")
        assert payload["dispatched"] is False
        assert payload["preview"] is True
        assert payload["mesh_capability"]["dispatches"] is False


class TestTheToolSurfaceSaysSo:
    def test_the_tool_docstring_states_the_absence(self) -> None:
        """The docstring is what the model reads before choosing an action."""
        from alpha.tools.builtins.capability_mesh_tool import capability_mesh_tool

        doc = " ".join(capability_mesh_tool.description.split())
        assert "Nothing is dispatched" in doc
        assert "no executor wiring" in doc
        assert "task" in doc and "RunManager" in doc, "the docstring must name the surface that does dispatch, or 'no wiring' reads as 'no alternative'"
        assert MESH_CAPABILITY in doc and MESH_CAPABILITY_STATE in doc

    @pytest.mark.asyncio
    async def test_the_disabled_refusal_names_the_absence(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """The default-off refusal is the first thing a caller ever sees.

        It already said the mesh "dispatches nothing"; it now also names the
        declared capability, so a caller that quotes the refusal carries the
        machine-readable state with it instead of having to infer it.
        """
        import importlib

        from langchain.tools import ToolRuntime

        from alpha.tools.builtins.capability_mesh_tool import capability_mesh_tool

        module = importlib.import_module("alpha.tools.builtins.capability_mesh_tool")
        config = CapabilityMeshConfig(enabled=False, directory=str(tmp_path))
        monkeypatch.setattr(module, "capability_mesh_config", lambda app_config=None: config)
        monkeypatch.setattr(module, "get_app_config", lambda: None)

        runtime = ToolRuntime(state={}, context={}, config={}, stream_writer=lambda _: None, tool_call_id="tc-1", store=None)
        text = await capability_mesh_tool.coroutine(runtime=runtime, action="passports")

        assert "disabled" in text
        assert "dispatches nothing" in text
        assert f"{MESH_CAPABILITY} = {MESH_CAPABILITY_STATE}" in text

    @pytest.mark.asyncio
    async def test_every_rendered_reply_states_the_wiring(self, monkeypatch: pytest.MonkeyPatch, mesh_dir: Path) -> None:
        """A read reply that omitted it would leave the model guessing.

        The header line travels on every action, including the reads, because
        "this mesh cannot route" is a property of the plane and not of the
        particular question asked.
        """
        import importlib

        from langchain.tools import ToolRuntime

        from alpha.tools.builtins.capability_mesh_tool import capability_mesh_tool

        module = importlib.import_module("alpha.tools.builtins.capability_mesh_tool")
        config = CapabilityMeshConfig(enabled=True, directory=str(mesh_dir))
        monkeypatch.setattr(module, "capability_mesh_config", lambda app_config=None: config)
        monkeypatch.setattr(module, "get_app_config", lambda: None)

        runtime = ToolRuntime(state={}, context={}, config={}, stream_writer=lambda _: None, tool_call_id="tc-1", store=None)
        marker = f"{MESH_CAPABILITY} = {MESH_CAPABILITY_STATE}"
        for action in ("passports", "memory", "calibration", "drift", "policy", "shadow"):
            text = await capability_mesh_tool.coroutine(runtime=runtime, action=action)
            assert marker in text, f"the {action} reply did not state the executor wiring"
        text = await capability_mesh_tool.coroutine(runtime=runtime, action="independence", entity_a="agent:planner", entity_b="agent:builder")
        assert marker in text
        for action in ("route", "team", "compose", "explain"):
            text = await capability_mesh_tool.coroutine(runtime=runtime, action=action, task="ship the parser", required_capabilities="debug")
            assert marker in text, f"the {action} preview did not state the executor wiring"
            assert "Nothing was dispatched" in text


class TestTheHttpSurfaceSaysSo:
    def test_the_capability_route_is_mounted_where_a_client_would_look(self) -> None:
        """A handler that is not mounted is not a surface.

        The declaration is the only way a client can learn the plane cannot
        dispatch without probing a data route first, so it has to be reachable
        at a predictable path — and it has to come before any catch-all, the
        same registration-order rule ``routers/skills.py`` follows.
        """
        from app.gateway.routers.capability_mesh import router

        paths = [route.path for route in router.routes]
        assert "/api/capability-mesh/capability" in paths
        catch_alls = [path for path in paths if "{" in path]
        assert not catch_alls, f"an unexpected catch-all exists and could swallow /capability: {catch_alls}"

    def test_the_capability_route_answers_even_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A client asking "could it dispatch?" is answered, not redirected.

        Every other route 503s while the mesh is disabled. That is right for a
        read of measurement data, and wrong for the one question that decides
        whether the client should have planned around the mesh at all — so this
        route is reachable either way and carries ``enabled`` alongside the
        declaration.
        """
        import asyncio

        from app.gateway.routers import capability_mesh as router_module

        monkeypatch.setattr(router_module, "get_app_config", lambda: _app_config(CapabilityMeshConfig(enabled=False)))
        payload = asyncio.run(router_module.get_capability(_user="admin"))
        assert payload["enabled"] is False
        assert payload["dispatches"] is False
        assert payload["state"] == "absent"
        assert "503" in payload["enabled_note"]

        monkeypatch.setattr(router_module, "get_app_config", lambda: _app_config(CapabilityMeshConfig(enabled=True)))
        payload = asyncio.run(router_module.get_capability(_user="admin"))
        assert payload["enabled"] is True
        assert payload["dispatches"] is False, "enabling must not add an executor"
        assert "does not add an executor" in payload["enabled_note"]


def _harness_path(module: str) -> Path:
    """``alpha.a.b`` -> the file or package directory under ``packages/harness``.

    Needed because ``alpha.subagents`` and ``alpha.agents`` are lazy facades:
    importing them yields an object with no ``__file__`` and no ``__spec__``, so
    a module-path probe has to go through the source tree.
    """
    assert module.startswith("alpha."), f"{module} is not a harness module path"
    relative = Path(*module.split(".")[1:])
    candidate = MESH_DIR.parent / relative.with_suffix(".py")
    return candidate if candidate.exists() else MESH_DIR.parent / relative


def _app_config(mesh: CapabilityMeshConfig):
    class _Config:
        capability_mesh = mesh

    return _Config()
