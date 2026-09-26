"""W-N1 plan-graph store proofs: append-only history + optimistic concurrency.

The store persists every graph revision of a workflow and rejects a writer
that is behind (the kernel's OCC rule) instead of losing its update. A
corrupt revision file is a loud failure, never an empty-looking history.
"""

from __future__ import annotations

import pytest

from alpha.workflow.models import WorkflowGraph
from alpha.workflow.plan_graph import PlanGraphError, PlanGraphStore, PlanVersionConflict


def _graph(version: int) -> WorkflowGraph:
    return WorkflowGraph(
        version=version,
        nodes={f"n{version}": {"id": f"n{version}", "prompt": f"work {version}"}},
        edges=[],
    )


@pytest.fixture
def store(tmp_path) -> PlanGraphStore:
    return PlanGraphStore(tmp_path / "plans")


def test_record_and_read_history(store: PlanGraphStore) -> None:
    first = store.record_revision("wf", _graph(1), source="register", note="initial", owner_id="owner-a")
    second = store.record_revision("wf", _graph(2), source="patch", note="add node")

    assert first.version == 1 and second.version == 2
    assert store.list_versions("wf") == [1, 2]
    assert [record.note for record in store.history("wf")] == ["initial", "add node"]
    assert store.latest("wf").version == 2
    assert store.get("wf", 1).graph.nodes["n1"].prompt == "work 1"
    assert store.get("wf", 99) is None
    assert store.history("wf", owner_id="owner-b") == []


def test_duplicate_revision_is_refused_history_is_append_only(store: PlanGraphStore) -> None:
    store.record_revision("wf", _graph(1))
    with pytest.raises(PlanVersionConflict, match="already exists"):
        store.record_revision("wf", _graph(1))
    assert store.list_versions("wf") == [1]


def test_compare_and_set_advances_and_rejects_stale_writer(store: PlanGraphStore) -> None:
    store.record_revision("wf", _graph(1))

    advanced = store.compare_and_set("wf", expected_version=1, new_graph=_graph(2), source="patch", note="ok")
    assert advanced.version == 2
    assert store.latest("wf").source == "patch"

    # A writer that still believes v1 is current is rejected, and NOTHING is
    # written: no lost update, no silent overwrite.
    with pytest.raises(PlanVersionConflict, match="expected latest v1, found v2"):
        store.compare_and_set("wf", expected_version=1, new_graph=_graph(3))
    assert store.list_versions("wf") == [1, 2]


def test_compare_and_set_rejects_a_different_owner(store: PlanGraphStore) -> None:
    store.record_revision("wf_owned", _graph(1), owner_id="owner-a")

    with pytest.raises(PlanVersionConflict, match="belongs to another owner"):
        store.compare_and_set(
            "wf_owned",
            expected_version=1,
            new_graph=_graph(2),
            owner_id="owner-b",
        )

    assert store.list_versions("wf_owned") == [1]
    assert store.history("wf_owned", owner_id="owner-b") == []


def test_compare_and_set_requires_a_real_advance(store: PlanGraphStore) -> None:
    store.record_revision("wf", _graph(3))
    with pytest.raises(PlanGraphError, match="must be greater than current"):
        store.compare_and_set("wf", expected_version=3, new_graph=_graph(3))
    with pytest.raises(PlanVersionConflict, match="expected latest v0, found v-1"):
        # A workflow with no revisions has current version -1; claiming to
        # advance from 0 is a mismatch, not a silent create.
        store.compare_and_set("wf_empty", expected_version=0, new_graph=_graph(1))


def test_corrupt_revision_is_a_loud_failure_not_an_empty_history(store: PlanGraphStore) -> None:
    store.record_revision("wf", _graph(1))
    store.revision_path("wf", 1).write_text('{"schema_version": 1, "workflow_id": "wf", ', encoding="utf-8")
    with pytest.raises(PlanGraphError, match="corrupt plan revision"):
        store.get("wf", 1)
    with pytest.raises(PlanGraphError, match="corrupt plan revision"):
        store.history("wf")


def test_unexpected_revision_filename_is_reported(store: PlanGraphStore) -> None:
    store.record_revision("wf", _graph(1))
    (store._workflow_dir("wf") / "vX.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PlanGraphError, match="unexpected revision filename"):
        store.list_versions("wf")


def test_workflow_id_is_path_safe(store: PlanGraphStore) -> None:
    for bad in ("../escape", "a/b", "", ".hidden", "x" * 129):
        with pytest.raises(PlanGraphError, match="invalid workflow_id"):
            store.record_revision(bad, _graph(1))


def test_save_is_atomic_leaving_no_temp_file(store: PlanGraphStore) -> None:
    store.record_revision("wf_atomic", _graph(1))
    directory = store._workflow_dir("wf_atomic")
    assert [path.name for path in directory.iterdir()] == ["v1.json"]
