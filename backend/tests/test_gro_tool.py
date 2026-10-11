"""GRO via the `swarm` tool: `compete` and `compete_status` actions.

The model-facing surface is two actions on the existing `swarm` tool, so
`BUILTIN_TOOLS`, `contracts/feature_manifest.json` and the capability counts
are untouched. These tests patch the seams a host owns — the candidate tool
pool, the execution adapter, the config and the store root — and drive the
real dispatch, runner and report code through `tool.invoke`.

Pinned behaviours:

- `compete` refuses honestly while `gro.enabled` is false, naming the config
  key; `compete_status` stays readable (it is read-only).
- The candidate pool strips the `swarm` tool itself (no nested tournaments);
  an empty pool is a refusal, never a silent tool-less candidate.
- Status reads never fabricate: an unknown id says "not found", a corrupt
  document is disclosed as unreadable, and a run interrupted before its
  document was written falls back to the live event-journal tail.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from alpha.gro.config import GroConfig
from alpha.gro.models import CandidateOutcome, CandidateStatus
from alpha.runtime.user_context import get_effective_user_id

# ``alpha.tools.builtins.swarm_tool`` as a package attribute is the *tool
# object* (the package re-exports it, shadowing the submodule), so resolve the
# real module through sys.modules.
swarm_tool_module = importlib.import_module("alpha.tools.builtins.swarm_tool")

GOAL = "Fix the parser bug in the tokenizer module"


class RecordingAdapter:
    """Stand-in for SubagentExecutionAdapter that records how it was built."""

    instances: list[RecordingAdapter] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.specs = []
        RecordingAdapter.instances.append(self)

    def run(self, spec):
        self.specs.append(spec)
        return CandidateOutcome(
            candidate_id=spec.candidate_id,
            status=CandidateStatus.COMPLETED,
            output_text=f"done {spec.candidate_id}",
            duration_ms=20.0,
        )


def _tool():
    return swarm_tool_module.swarm_tool


@pytest.fixture(autouse=True)
def _reset_recorder():
    RecordingAdapter.instances.clear()
    yield
    RecordingAdapter.instances.clear()


@pytest.fixture()
def gro_env(tmp_path, monkeypatch):
    """Patch the host seams: config, tool pool, adapter, and store root."""

    import alpha.config as alpha_config
    import alpha.gro.adapters as adapters_module
    import alpha.gro.store as store_module

    real_store = store_module.GroStore

    cfg = GroConfig(
        enabled=True,
        default_mode="single",
        max_candidates_per_task=3,
        max_active_candidates_global=1,
        candidate_timeout_seconds=30.0,
        run_timeout_seconds=120.0,
    )
    monkeypatch.setattr(alpha_config, "get_app_config", lambda: SimpleNamespace(gro=cfg))
    monkeypatch.setattr(swarm_tool_module, "_gro_assemble_tools", lambda: [SimpleNamespace(name="bash"), SimpleNamespace(name="write_file"), SimpleNamespace(name="swarm")])
    monkeypatch.setattr(adapters_module, "SubagentExecutionAdapter", RecordingAdapter)
    monkeypatch.setattr(store_module, "GroStore", lambda: real_store(root=tmp_path))
    return SimpleNamespace(store_factory=lambda: real_store(root=tmp_path), tmp_path=tmp_path)


def _invoke(payload: dict) -> str:
    return _tool().invoke(payload)


class TestSurface:
    def test_one_swarm_tool_carries_the_two_gro_actions(self):
        tool = _tool()
        assert tool.name == "swarm"
        actions = set(tool.args["action"]["enum"])
        assert {"compete", "compete_status"} <= actions

    def test_malformed_items_json_is_refused_before_any_work(self, gro_env):
        text = _invoke({"action": "compete", "goal": GOAL, "items_json": "{not an array}"})
        assert "Error: 'items_json' must be a valid JSON array." in text
        assert RecordingAdapter.instances == []


class TestCompeteRefusals:
    def test_refused_while_disabled_naming_the_config_key(self, monkeypatch, tmp_path):
        import alpha.config as alpha_config
        import alpha.gro.store as store_module

        real_store = store_module.GroStore
        monkeypatch.setattr(alpha_config, "get_app_config", lambda: SimpleNamespace(gro=GroConfig(enabled=False)))
        monkeypatch.setattr(store_module, "GroStore", lambda: real_store(root=tmp_path))

        text = _invoke({"action": "compete", "goal": GOAL})
        assert "Error: group-relative orchestration is disabled" in text
        assert "gro.enabled" in text
        assert "compete_status" in text  # the read path stays offered
        assert RecordingAdapter.instances == []

    def test_goal_is_required(self, gro_env):
        text = _invoke({"action": "compete", "goal": "   "})
        assert "Error: 'goal' parameter is required" in text
        assert RecordingAdapter.instances == []

    def test_empty_tool_pool_is_a_refusal_not_a_tool_less_candidate(self, monkeypatch, gro_env):
        monkeypatch.setattr(swarm_tool_module, "_gro_assemble_tools", lambda: [])
        text = _invoke({"action": "compete", "goal": GOAL})
        assert "assembled empty" in text
        assert RecordingAdapter.instances == []

    def test_assembly_failure_is_data_not_an_escape(self, monkeypatch, gro_env):
        def boom():
            raise RuntimeError("MCP catalog unavailable")

        monkeypatch.setattr(swarm_tool_module, "_gro_assemble_tools", boom)
        text = _invoke({"action": "compete", "goal": GOAL})
        assert "could not be assembled" in text
        assert "MCP catalog unavailable" in text
        assert RecordingAdapter.instances == []


class TestCompeteRun:
    def test_runs_a_group_and_strips_the_swarm_tool_from_the_pool(self, gro_env):
        text = _invoke({"action": "compete", "goal": GOAL})
        assert "GRO run" in text
        assert "**selected**" in text
        assert "UNVERIFIED" in text  # no criteria declared -> never claimed verified

        adapter = RecordingAdapter.instances[0]
        pool_names = {getattr(tool, "name", None) for tool in adapter.kwargs["tools"]}
        assert pool_names == {"bash", "write_file"}  # nested tournaments are impossible
        assert adapter.kwargs["run_id"].startswith("gro_")
        assert adapter.kwargs["user_id"] == get_effective_user_id()

    def test_the_run_is_persisted_owner_scoped(self, gro_env):
        _invoke({"action": "compete", "goal": GOAL})
        run_id = RecordingAdapter.instances[0].kwargs["run_id"]
        store = gro_env.store_factory()
        run = store.load(run_id, owner_id=get_effective_user_id())
        assert run is not None
        assert store.load(run_id, owner_id="someone-else") is None


class TestCompeteStatus:
    def test_reads_back_a_persisted_run(self, gro_env):
        _invoke({"action": "compete", "goal": GOAL})
        run_id = RecordingAdapter.instances[0].kwargs["run_id"]
        text = _invoke({"action": "compete_status", "swarm_id": run_id})
        assert f"GRO run `{run_id}`" in text
        assert "**selected**" in text

    def test_lists_recent_runs_when_no_id_is_given(self, gro_env):
        _invoke({"action": "compete", "goal": GOAL})
        run_id = RecordingAdapter.instances[0].kwargs["run_id"]
        text = _invoke({"action": "compete_status"})
        assert "GRO runs (newest first)" in text
        assert run_id in text

    def test_stays_readable_while_disabled(self, monkeypatch, tmp_path):
        import alpha.config as alpha_config
        import alpha.gro.store as store_module

        real_store = store_module.GroStore
        monkeypatch.setattr(alpha_config, "get_app_config", lambda: SimpleNamespace(gro=GroConfig(enabled=False)))
        monkeypatch.setattr(store_module, "GroStore", lambda: real_store(root=tmp_path))

        text = _invoke({"action": "compete_status"})
        assert "No GRO runs recorded" in text
        assert "disabled" not in text

    def test_unknown_run_id_says_not_found(self, gro_env):
        text = _invoke({"action": "compete_status", "swarm_id": "gro_doesnotexist"})
        assert "No GRO run 'gro_doesnotexist' found" in text

    def test_invalid_run_id_is_refused_not_touching_the_store(self, gro_env):
        text = _invoke({"action": "compete_status", "swarm_id": "../../etc/passwd"})
        assert "Error: invalid GRO run id" in text

    def test_corrupt_document_is_disclosed_never_read_as_no_runs(self, gro_env):
        _invoke({"action": "compete", "goal": GOAL})
        run_id = RecordingAdapter.instances[0].kwargs["run_id"]
        store = gro_env.store_factory()
        store._path(get_effective_user_id(), run_id).write_text("{not json", encoding="utf-8")

        text = _invoke({"action": "compete_status", "swarm_id": run_id})
        assert "could not be read" in text

    def test_interrupted_run_falls_back_to_the_event_journal(self, gro_env):
        store = gro_env.store_factory()
        owner = get_effective_user_id()
        assert store.append_event(owner_id=owner, run_id="gro_journal", event={"type": "run.started", "goal": GOAL}) is True

        text = _invoke({"action": "compete_status", "swarm_id": "gro_journal"})
        assert "No state document" in text
        assert "Event journal tail" in text
        assert "run.started" in text
