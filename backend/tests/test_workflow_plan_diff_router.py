"""REST surface for graph-revision diffs.

Direct-handler pattern (same as ``test_workflow_durability_router``), with
``ALPHA_HOME`` redirected per test so the plan store is always a temp dir.

Honesty pins in this suite:
* the earliest revision reports **400 naming the missing base** rather than an
  empty "identical" diff, which would assert an equality nobody measured;
* direction is preserved — comparing v3 down to v1 reports nodes as *removed*,
  not symmetrised into a forward answer;
* credentials inside node config never reach the response;
* an unknown or differently-owned revision is 404, never a partial leak.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from alpha.workflow.models import NodeStatus, WorkflowEdge, WorkflowGraph, WorkflowNode
from app.gateway.routers.workflows import diff_workflow_plan


@pytest.fixture(autouse=True)
def _isolate_alpha(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))


def _store():
    from alpha.config.runtime_paths import runtime_home
    from alpha.workflow.plan_graph import PlanGraphStore

    return PlanGraphStore(runtime_home() / "workflow_store" / "plans")


def _graph(version: int, node_ids: list[str], *, config: dict | None = None) -> WorkflowGraph:
    graph = WorkflowGraph(version=version)
    for node_id in node_ids:
        graph.add_node(WorkflowNode(id=node_id, config=dict(config or {})))
    for index in range(len(node_ids) - 1):
        graph.add_edge(WorkflowEdge(source=node_ids[index], target=node_ids[index + 1]))
    return graph


def _seed(workflow_id: str, version: int, node_ids: list[str], *, note: str = "", source: str = "manual", config: dict | None = None):
    return _store().record_revision(
        workflow_id,
        _graph(version, node_ids, config=config),
        version=version,
        note=note,
        source=source,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_diff_reports_the_change_and_its_recorded_reason() -> None:
    _seed("wf_diff", 1, ["research"])
    _seed("wf_diff", 2, ["research", "verify"], note="inserted a verification step after the flaky research pass", source="patch")

    payload = await diff_workflow_plan("wf_diff", 2, MagicMock())

    assert payload["base_version"] == 1
    assert payload["target_version"] == 2
    assert payload["has_plan_change"] is True
    assert payload["identical"] is False
    # The reason comes from the record; it is not invented.
    assert payload["reason"] == "inserted a verification step after the flaky research pass"
    assert payload["source"] == "patch"

    kinds = [change["kind"] for change in payload["changes"]]
    assert "node_added" in kinds
    added = next(change for change in payload["changes"] if change["kind"] == "node_added")
    assert added["key"] == "verify"
    assert added["structural"] is True
    assert added["summary"] == "node added: verify"
    assert payload["counts"]["plan_changes"] >= 1


@pytest.mark.asyncio
async def test_earliest_revision_reports_the_missing_base_not_fake_equality() -> None:
    """v1 has nothing earlier. An empty `identical: true` would be a lie."""
    _seed("wf_first", 1, ["a"])

    with pytest.raises(HTTPException) as exc_info:
        await diff_workflow_plan("wf_first", 1, MagicMock())

    assert exc_info.value.status_code == 400
    assert "no earlier revision" in str(exc_info.value.detail)
    assert "base=" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_explicit_base_selects_the_named_pair() -> None:
    _seed("wf_span", 1, ["a"])
    _seed("wf_span", 2, ["a", "b"], note="second step")
    _seed("wf_span", 3, ["a", "b", "c"], note="third step")

    payload = await diff_workflow_plan("wf_span", 3, MagicMock(), base=1)

    assert payload["base_version"] == 1
    assert payload["target_version"] == 3
    added_keys = {change["key"] for change in payload["changes"] if change["kind"] == "node_added"}
    assert added_keys == {"b", "c"}
    # reason is the TARGET revision's note — the one whose creation it explains
    assert payload["reason"] == "third step"


@pytest.mark.asyncio
async def test_backwards_comparison_reports_going_backwards() -> None:
    _seed("wf_back", 1, ["a"])
    _seed("wf_back", 3, ["a", "b"])

    payload = await diff_workflow_plan("wf_back", 1, MagicMock(), base=3)

    assert payload["base_version"] == 3
    assert payload["target_version"] == 1
    kinds = [change["kind"] for change in payload["changes"]]
    assert "node_removed" in kinds
    assert "node_added" not in kinds, "a backwards diff must not be silently rewritten as a forward one"


@pytest.mark.asyncio
async def test_unknown_revision_is_404() -> None:
    _seed("wf_missing", 1, ["a"])

    with pytest.raises(HTTPException) as exc_info:
        await diff_workflow_plan("wf_missing", 9, MagicMock())

    assert exc_info.value.status_code == 404
    assert "v9" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_unknown_base_revision_is_404() -> None:
    _seed("wf_badbase", 3, ["a"])

    with pytest.raises(HTTPException) as exc_info:
        await diff_workflow_plan("wf_badbase", 3, MagicMock(), base=2)

    assert exc_info.value.status_code == 404
    assert "v2" in str(exc_info.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [0, -1])
async def test_non_positive_revision_is_rejected_with_the_real_reason(version: int) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await diff_workflow_plan("wf_any", version, MagicMock())

    assert exc_info.value.status_code == 400
    assert "must be >= 1" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_identical_revisions_report_identical() -> None:
    _seed("wf_same", 1, ["a", "b"])
    _seed("wf_same", 2, ["a", "b"])

    payload = await diff_workflow_plan("wf_same", 2, MagicMock())

    assert payload["identical"] is True
    assert payload["has_plan_change"] is False
    assert payload["changes"] == []


@pytest.mark.asyncio
async def test_credentials_in_node_config_never_reach_the_response() -> None:
    """The event log is redacted; a diff endpoint must not be the side door."""
    _seed("wf_secret", 1, ["call"], config={"endpoint": "https://api.internal"})
    _seed(
        "wf_secret",
        2,
        ["call"],
        note="point the tool at production",
        config={"endpoint": "https://prod.internal", "api_key": "sk-live-do-not-leak"},
    )

    payload = await diff_workflow_plan("wf_secret", 2, MagicMock())
    text = str(payload)

    assert "sk-live-do-not-leak" not in text
    assert "https://prod.internal" in text, "redaction must not swallow the actual change"


@pytest.mark.asyncio
async def test_runtime_state_change_is_not_reported_as_a_plan_change() -> None:
    base = _graph(1, ["build"])
    target = _graph(2, ["build"])
    target.nodes["build"].status = NodeStatus.SUCCEEDED

    store = _store()
    store.record_revision("wf_runtime", base, version=1, note="register", source="register")
    store.record_revision("wf_runtime", target, version=2, note="run completed", source="manual")

    payload = await diff_workflow_plan("wf_runtime", 2, MagicMock())

    assert payload["identical"] is False
    assert payload["has_plan_change"] is False
    assert payload["counts"]["plan_changes"] == 0
    assert payload["counts"]["runtime_changes"] == 1
    assert payload["changes"][0]["structural"] is False


def test_diff_route_is_registered() -> None:
    """The route must exist exactly once, as GET, under the router prefix."""
    from app.gateway.routers.workflows import router

    matches = [route for route in router.routes if getattr(route, "path", "").endswith("/{workflow_id}/plans/{version}/diff")]
    assert len(matches) == 1, "the plan-diff route must be registered exactly once"
    assert matches[0].path == "/api/workflows/{workflow_id}/plans/{version}/diff"
    assert "GET" in matches[0].methods
