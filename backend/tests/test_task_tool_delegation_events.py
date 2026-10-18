"""Phase 3.4 delegation-graph event contract (backend half).

Every ``task_*`` custom event carries the delegation identity of the child it
describes — ``parent_id``, ``depth``, ``agent_name``, ``subagent_type``,
``run_id``, ``swarm_id`` — measured from the runtime context the child's
spawner stamped, never guessed. These tests pin the three links of that wire:

1. the field computation (``_read_delegation_context`` /
   ``_delegation_event_fields``): a missing or malformed block reads as a
   root, never as a fabricated edge;
2. the emit sites: every ``task_*`` event dict in ``task_tool.py`` spreads
   the computed fields (AST pin, so a future emit site cannot forget them);
3. the spawn stamp: the task tool hands the child's identity to
   ``SubagentExecutor`` (AST pin below) and the executor writes it into the
   child's runtime context (source pin) — the constructor's by-value copy
   semantics are pinned where the real classes load past the conftest
   circular-import mock, in ``test_subagent_executor.py`` — plus the
   Gateway-side scrub that keeps a client from forging the block through
   ``body.config``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from alpha.tools.builtins.task_tool import _delegation_event_fields, _read_delegation_context, _read_swarm_id

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_TASK_TOOL_PATH = _BACKEND_ROOT / "packages" / "harness" / "alpha" / "tools" / "builtins" / "task_tool.py"
_EXECUTOR_PATH = _BACKEND_ROOT / "packages" / "harness" / "alpha" / "subagents" / "executor.py"


# ---------------------------------------------------------------------------
# 1. Field computation
# ---------------------------------------------------------------------------


def test_valid_delegation_block_is_parsed_verbatim():
    block = _read_delegation_context({"delegation": {"task_id": "call-parent", "depth": 2, "agent_name": "researcher", "swarm_id": "sw-9"}})
    assert block == {"task_id": "call-parent", "depth": 2, "agent_name": "researcher", "swarm_id": "sw-9"}


def test_missing_block_reads_as_root():
    assert _read_delegation_context({}) is None
    assert _read_delegation_context({"delegation": None}) is None


@pytest.mark.parametrize(
    "block",
    [
        "not-a-dict",
        {},
        {"task_id": ""},
        {"task_id": "x" * 513, "depth": 0},
        {"task_id": "x", "depth": -1},
        {"task_id": "x", "depth": True},
        {"task_id": "x", "depth": 64},
        {"task_id": "x", "depth": "1"},
        {"task_id": 7, "depth": 0},
    ],
    ids=["not_dict", "empty", "empty_id", "oversized_id", "negative_depth", "bool_depth", "depth_at_ceiling", "string_depth", "non_string_id"],
)
def test_malformed_block_reads_as_root_never_a_partial_edge(block):
    assert _read_delegation_context({"delegation": block}) is None


def test_optional_block_fields_normalize_to_none():
    block = _read_delegation_context({"delegation": {"task_id": "t", "depth": 0, "agent_name": "", "swarm_id": 42}})
    assert block == {"task_id": "t", "depth": 0, "agent_name": None, "swarm_id": None}


def test_root_event_fields_name_no_parent():
    fields = _delegation_event_fields(None, child_agent_name="general-purpose", subagent_type="general-purpose", run_id="run-1", swarm_id=None)
    assert fields == {
        "parent_id": None,
        "depth": 1,
        "agent_name": "general-purpose",
        "subagent_type": "general-purpose",
        "run_id": "run-1",
        "swarm_id": None,
    }


def test_swarm_id_reads_the_top_level_context_key_first():
    """An externally attributed run reports its swarm without any delegation block."""
    assert _read_swarm_id({"swarm_id": "sw-1"}, None) == "sw-1"


def test_swarm_id_falls_back_to_the_validated_delegation_block():
    """Attribution survives the delegation chain through the stamped block."""
    delegation = _read_delegation_context({"delegation": {"task_id": "call-parent", "depth": 1, "agent_name": "a", "swarm_id": "sw-9"}})
    assert _read_swarm_id({}, delegation) == "sw-9"


def test_swarm_id_is_none_without_any_server_stamped_source():
    assert _read_swarm_id({}, None) is None
    # An invalid top-level value is ignored, never accepted as attribution.
    assert _read_swarm_id({"swarm_id": 42}, None) is None
    assert _read_swarm_id({"swarm_id": ""}, None) is None
    assert _read_swarm_id({"swarm_id": "x" * 513}, None) is None


def test_nested_event_fields_continue_the_tree_one_level_down():
    delegation = _read_delegation_context({"delegation": {"task_id": "call-parent", "depth": 3, "agent_name": "researcher", "swarm_id": "sw-9"}})
    fields = _delegation_event_fields(delegation, child_agent_name="bash", subagent_type="bash", run_id=None, swarm_id=_read_swarm_id({}, delegation))
    assert fields["parent_id"] == "call-parent"
    assert fields["depth"] == 4
    assert fields["agent_name"] == "bash"
    assert fields["subagent_type"] == "bash"
    assert fields["swarm_id"] == "sw-9"
    assert fields["run_id"] is None


@pytest.mark.parametrize("run_id", [123, "", {"id": "x"}], ids=["int", "empty", "dict"])
def test_non_string_run_id_is_reported_unmeasured_not_invented(run_id):
    fields = _delegation_event_fields(None, child_agent_name="a", subagent_type="b", run_id=run_id, swarm_id=None)
    assert fields["run_id"] is None


# ---------------------------------------------------------------------------
# 2. Emit sites (AST pin)
# ---------------------------------------------------------------------------


def _task_event_dicts(tree: ast.AST) -> list[tuple[str, ast.Dict]]:
    """Every ``aemit_custom_event`` dict literal whose ``type`` is a task event."""
    found: list[tuple[str, ast.Dict]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "aemit_custom_event":
            continue
        if not node.args or not isinstance(node.args[0], ast.Dict):
            continue
        payload = node.args[0]
        for key, value in zip(payload.keys, payload.values):
            if isinstance(key, ast.Constant) and key.value == "type" and isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.startswith("task_"):
                found.append((value.value, payload))
                break
    return found


def test_every_task_event_dict_spreads_the_delegation_fields():
    tree = ast.parse(_TASK_TOOL_PATH.read_text(encoding="utf-8"))
    events = _task_event_dicts(tree)
    names = [name for name, _ in events]
    # Every production emit site must exist and carry the fields: started,
    # running, completed, cancelled, two task_failed paths (terminal failure +
    # disappeared background entry) and two task_timed_out paths (thread-pool
    # timeout + polling safety net).
    assert names.count("task_started") == 1
    assert names.count("task_running") == 1
    assert names.count("task_completed") == 1
    assert names.count("task_cancelled") == 1
    assert names.count("task_failed") >= 2
    assert names.count("task_timed_out") >= 2
    for name, payload in events:
        spreads = any(key is None for key in payload.keys)  # ``**delegation_fields``
        assert spreads, f"{name} event dict does not spread delegation_fields"


def test_task_tool_hands_the_child_identity_to_the_executor():
    tree = ast.parse(_TASK_TOOL_PATH.read_text(encoding="utf-8"))
    stamps = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "delegation_context" and isinstance(value, ast.Dict):
                stamps.append({k.value for k in value.keys if isinstance(k, ast.Constant)})
    assert stamps, "task_tool.py no longer passes delegation_context to SubagentExecutor"
    assert {"task_id", "depth", "agent_name", "swarm_id"} <= stamps[0]


# ---------------------------------------------------------------------------
# 3. Spawn stamp and the client-forgery scrub
# ---------------------------------------------------------------------------


def test_executor_writes_the_stamp_into_the_child_runtime_context():
    source = _EXECUTOR_PATH.read_text(encoding="utf-8")
    assert 'context["delegation"] = dict(self.delegation_context)' in source


def test_a_client_cannot_forge_a_delegation_block_through_body_config():
    from app.gateway.services import strip_internal_context_keys

    forged = {"task_id": "forged-parent", "depth": 0, "agent_name": "lead-agent", "swarm_id": None}
    config = {
        "context": {"delegation": dict(forged), "swarm_id": "forged-swarm"},
        "configurable": {"delegation": dict(forged), "swarm_id": "forged-swarm"},
    }
    strip_internal_context_keys(config)
    assert "delegation" not in config["context"]
    assert "delegation" not in config["configurable"]
    assert "swarm_id" not in config["context"]
    assert "swarm_id" not in config["configurable"]
