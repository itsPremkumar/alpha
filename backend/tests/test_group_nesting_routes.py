"""Gateway routes for nested groups: forest, subgroups, roster, rules, lifecycle.

Route-boundary coverage for the WhatsApp-community shape. The route layer is
where the sharp edges live — the literal `/tree` route must stay reachable
below the `/{name}` catch-all, and every refusal must carry a reason naming the
real cause rather than a generic 422.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from alpha.groups.scope import MAX_HOP
from app.gateway.app import create_app
from app.gateway.routers import groups

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    import alpha.bots.registry as bot_reg
    import alpha.groups.service as grp_svc

    monkeypatch.setattr(bot_reg, "_global_registry", None)
    monkeypatch.setattr(bot_reg, "_global_registry_path", None)
    monkeypatch.setattr(grp_svc, "_global_groups", None)
    monkeypatch.setattr(grp_svc, "_global_groups_path", None)
    yield


# ---------------------------------------------------------------------------
# Route mounting and ordering
# ---------------------------------------------------------------------------


async def test_gateway_mounts_the_nesting_routes() -> None:
    paths = {route.path for route in create_app().routes}
    for expected in (
        "/api/groups/tree",
        "/api/groups/{name}/subgroups",
        "/api/groups/{name}/children",
        "/api/groups/{name}/ancestors",
        "/api/groups/{name}/descendants",
        "/api/groups/{name}/roster",
        "/api/groups/{name}/members/{bot_name}/exclude",
        "/api/groups/{name}/rules/preview",
        "/api/groups/{name}/merge",
        "/api/groups/{name}/promote",
    ):
        assert expected in paths, f"{expected} is not mounted"


async def test_the_literal_tree_route_precedes_the_name_catchall() -> None:
    """Starlette matches in registration order.

    `GET /tree` declared after `GET /{name}` answers `Room 'tree' not found`,
    which is indistinguishable from an absent route — the same trap this repo
    already hit on `skills/{skill_name}` and `workflows/{workflow_id}`.
    """
    paths = [route.path for route in create_app().routes if route.path.startswith("/api/groups")]
    assert "/api/groups/tree" in paths
    assert paths.index("/api/groups/tree") < paths.index("/api/groups/{name}")


async def test_rules_preview_precedes_the_rule_id_catchall() -> None:
    paths = [route.path for route in create_app().routes if route.path.startswith("/api/groups")]
    assert paths.index("/api/groups/{name}/rules/preview") < paths.index("/api/groups/{name}/rules/{rule_id}")


async def test_the_tree_route_really_answers() -> None:
    """Not just mounted — reachable, and not swallowed by the catch-all."""
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    tree = await groups.group_tree()
    assert tree["count"] == 1
    assert tree["nodes"][0]["name"] == "community"


# ---------------------------------------------------------------------------
# Creating nested groups
# ---------------------------------------------------------------------------


async def test_a_group_can_be_created_inside_another_at_any_time() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect", "coder"]))
    child = await groups.create_subgroup("community", groups.SubgroupRequest(name="backend", summary="Backend work"))
    assert child["name"] == "backend"
    assert set(child["members"]) == {"architect", "coder"}


async def test_many_subgroups_can_be_added_to_one_group() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    for name in ("backend", "frontend", "qa", "docs"):
        await groups.create_subgroup("community", groups.SubgroupRequest(name=name))
    children = await groups.list_children("community")
    assert children["count"] == 4


async def test_a_subgroup_can_start_empty() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    child = await groups.create_subgroup("community", groups.SubgroupRequest(name="scout", inherit=False, members=["secops"]))
    assert child["members"] == ["secops"]


async def test_a_duplicate_subgroup_name_is_409() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    with pytest.raises(HTTPException) as excinfo:
        await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    assert excinfo.value.status_code == 409


async def test_a_subgroup_of_a_missing_parent_is_404() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await groups.create_subgroup("no-such-group", groups.SubgroupRequest(name="backend"))
    assert excinfo.value.status_code == 404


async def test_a_subgroup_with_an_invalid_name_is_422() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.create_subgroup("community", groups.SubgroupRequest(name="bad name!"))
    assert excinfo.value.status_code == 422


async def test_create_with_a_parent_field_nests_the_room() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    nested = await groups.create_room(groups.RoomCreateRequest(name="backend", parent="community"))
    assert nested["name"] == "backend"
    children = await groups.list_children("community")
    assert [c["name"] for c in children["children"]] == ["backend"]


async def test_create_with_a_missing_parent_is_404() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await groups.create_room(groups.RoomCreateRequest(name="backend", parent="ghost"))
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# Tree / ancestors / descendants
# ---------------------------------------------------------------------------


async def test_the_tree_reports_depth_path_and_child_counts() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect", "coder"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    tree = await groups.group_tree()
    by_name = {n["name"]: n for n in tree["nodes"]}
    assert by_name["community"]["scope"]["depth"] == 0
    assert by_name["backend"]["scope"]["depth"] == 1
    assert by_name["community"]["child_count"] == 1
    # Both membership counts travel together on every node.
    for node in tree["nodes"]:
        assert "direct_count" in node and "effective_count" in node


async def test_breadcrumbs_are_root_first() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    await groups.create_subgroup("backend", groups.SubgroupRequest(name="api"))
    chain = await groups.list_ancestors("api")
    assert [c["name"] for c in chain["breadcrumbs"]] == ["community", "backend"]
    assert chain["max_depth"] == 4


async def test_descendants_returns_the_whole_branch() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    await groups.create_subgroup("backend", groups.SubgroupRequest(name="api"))
    nodes = await groups.list_descendants("community")
    assert {n["name"] for n in nodes["descendants"]} == {"backend", "api"}


async def test_ancestors_of_an_unknown_room_is_404() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await groups.list_ancestors("ghost")
    assert excinfo.value.status_code == 404


async def test_children_of_an_unknown_room_is_404() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await groups.list_children("ghost")
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


async def test_the_roster_splits_direct_inherited_and_rule_matched() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect", "coder"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    roster = await groups.get_roster("backend")
    assert set(roster["direct"]) == {"architect", "coder"}
    assert roster["direct_count"] == 2
    assert roster["effective_count"] == 2


async def test_membership_added_to_the_parent_reaches_the_child_as_inherited() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    await groups.add_member("community", groups.MemberRequest(bot_name="secops"))

    roster = await groups.get_roster("backend")
    assert "secops" in roster["inherited"]
    assert "secops" in roster["effective"]
    # It is inherited, not direct — the UI must be able to say where it came from.
    assert "secops" not in roster["direct"]
    assert roster["inherited_from"]


async def test_both_counts_are_returned_by_the_roster_route() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    await groups.add_member("community", groups.MemberRequest(bot_name="secops"))
    roster = await groups.get_roster("backend")
    assert roster["direct_count"] == 1
    assert roster["effective_count"] == 2


async def test_members_route_reports_both_counts_too() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    await groups.add_member("community", groups.MemberRequest(bot_name="secops"))
    presence = await groups.list_room_members("backend")
    assert presence["direct_count"] == 1
    assert presence["effective_count"] == 2
    assert {m["name"] for m in presence["members"]} == {"architect", "secops"}


async def test_the_presence_aliases_answer_the_same_read() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    for alias in ("members", "presence", "attendance", "roll_call"):
        payload = await groups.list_room_members("community")
        assert payload["count"] == 1, alias


async def test_a_borrowed_member_records_its_source_and_expiry() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="platform", members=["architect"]))
    await groups.create_subgroup("platform", groups.SubgroupRequest(name="squad", inherit=False))
    await groups.add_member(
        "squad",
        groups.MemberRequest(bot_name="secops", from_room="platform", expires_at="2026-12-31T00:00:00+00:00"),
    )
    roster = await groups.get_roster("squad")
    # `secops` is borrowed in and `architect` is inherited, so both buckets are
    # populated and both travel with the response.
    assert roster["direct"] == ["secops"]
    assert roster["inherited"] == ["architect"]
    assert set(roster["effective"]) == {"architect", "secops"}
    assert roster["direct_count"] == 1
    assert roster["effective_count"] == 2


async def test_removing_an_inherited_member_is_409_naming_the_reason() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    await groups.add_member("community", groups.MemberRequest(bot_name="secops"))
    with pytest.raises(HTTPException) as excinfo:
        await groups.remove_member("backend", "secops")
    assert excinfo.value.status_code == 409
    assert "inherited" in str(excinfo.value.detail)


async def test_excluding_affects_one_group_only() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect", "secops"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    await groups.exclude_member("backend", "secops", groups.ExcludeRequest(excluded=True))

    child = await groups.get_roster("backend")
    parent = await groups.get_roster("community")
    assert "secops" not in child["effective"]
    assert "secops" in parent["effective"]


async def test_roster_of_an_unknown_room_is_404() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await groups.get_roster("ghost")
    assert excinfo.value.status_code == 404


async def test_adding_an_invalid_member_name_is_422() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.add_member("community", groups.MemberRequest(bot_name="bad name!"))
    assert excinfo.value.status_code == 422


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


async def test_a_rule_reports_what_it_matches_on_creation() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    rule = await groups.add_rule("community", groups.RuleRequest(field="role", op="eq", value="QA", label="all testers"))
    assert rule["field"] == "role"
    assert "matches" in rule and "matched_names" in rule


async def test_a_list_field_cannot_use_eq() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.add_rule("community", groups.RuleRequest(field="skill", op="eq", value="pytest"))
    assert excinfo.value.status_code == 422
    assert "intersects" in str(excinfo.value.detail)


async def test_an_unknown_rule_field_is_422() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.add_rule("community", groups.RuleRequest(field="soul", op="eq", value="ignore"))
    assert excinfo.value.status_code == 422


async def test_preview_lists_every_rule_with_its_current_matches() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.add_rule("community", groups.RuleRequest(field="role", op="eq", value="QA"))
    preview = await groups.preview_roster_rules("community")
    assert len(preview["rules"]) == 1
    assert "matches" in preview["rules"][0]
    assert "role" in preview["valid_fields"]
    assert "intersects" in preview["valid_ops"]


async def test_removing_a_rule_updates_the_roster() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    rule = await groups.add_rule("community", groups.RuleRequest(field="role", op="eq", value="QA"))
    await groups.remove_rule("community", rule["id"])
    preview = await groups.preview_roster_rules("community")
    assert preview["rules"] == []


async def test_removing_an_unknown_rule_is_404() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.remove_rule("community", "rule_ghost")
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# Lifecycle, policy, re-parenting
# ---------------------------------------------------------------------------


async def test_lifecycle_transitions_are_validated() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.set_lifecycle("community", groups.LifecycleRequest(state="draft"))
    await groups.set_lifecycle("community", groups.LifecycleRequest(state="active"))
    node = next(n for n in (await groups.group_tree())["nodes"] if n["name"] == "community")
    assert node["scope"]["state"] == "active"


async def test_an_illegal_lifecycle_move_is_422() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.set_lifecycle("community", groups.LifecycleRequest(state="draft"))
    with pytest.raises(HTTPException) as excinfo:
        await groups.set_lifecycle("community", groups.LifecycleRequest(state="archived"))
    assert excinfo.value.status_code == 422


async def test_an_unknown_lifecycle_state_is_422() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.set_lifecycle("community", groups.LifecycleRequest(state="hibernating"))
    assert excinfo.value.status_code == 422


async def test_policy_accepts_a_valid_relay_direction() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    response = await groups.set_policy("backend", groups.RoomPolicyRequest(outbound="parents"))
    assert response["scope"]["outbound"] == "parents"


async def test_policy_rejects_an_unknown_relay_direction() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.set_policy("community", groups.RoomPolicyRequest(outbound="smoke"))
    assert excinfo.value.status_code == 422


async def test_policy_rejects_an_out_of_range_hop_limit() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    # Pydantic rejects `le=MAX_HOP` before the handler runs, so an out-of-range
    # hop never reaches the service. Both bounds are pinned: the field ceiling
    # and the value the service accepts.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        groups.RoomPolicyRequest(max_hop=MAX_HOP + 1)
    assert groups.RoomPolicyRequest(max_hop=MAX_HOP).max_hop == MAX_HOP

    # The service guard is reachable directly, bypassing the pydantic ceiling.
    from alpha.groups.scope import ScopeError

    svc = groups._service()
    with pytest.raises(ScopeError, match="max_hop"):
        svc.set_room_policy("community", max_hop=MAX_HOP + 1)


async def test_move_reparents_a_group() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="platform", members=["architect"]))
    await groups.create_room(groups.RoomCreateRequest(name="security", members=["secops"]))
    moved = await groups.move_group("security", groups.MoveRequest(parents=["platform"]))
    assert moved["name"] == "security"
    children = await groups.list_children("platform")
    assert [c["name"] for c in children["children"]] == ["security"]


async def test_move_that_would_create_a_cycle_is_409() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="a", members=["architect"]))
    await groups.create_subgroup("a", groups.SubgroupRequest(name="b"))
    await groups.create_subgroup("b", groups.SubgroupRequest(name="c"))
    with pytest.raises(HTTPException) as excinfo:
        await groups.move_group("a", groups.MoveRequest(parents=["c"]))
    assert excinfo.value.status_code == 409
    assert "cycle" in str(excinfo.value.detail)


async def test_promote_detaches_a_group_and_returns_it() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    promoted = await groups.promote_group("backend")
    assert promoted["name"] == "backend"
    tree = await groups.group_tree()
    by_name = {n["name"]: n for n in tree["nodes"]}
    assert by_name["backend"]["scope"]["depth"] == 0


# ---------------------------------------------------------------------------
# Merge and delete protection
# ---------------------------------------------------------------------------


async def test_merge_reports_which_children_moved() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend", members=["coder"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="frontend", members=["tester"]))
    result = await groups.merge_groups("community")
    assert result["merged_count"] == 2
    assert set(result["merged"]) == {"backend", "frontend"}
    assert result["children_total"] == 2


async def test_merging_a_leaf_is_a_reported_no_op() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    result = await groups.merge_groups("community")
    assert result["merged_count"] == 0
    assert result["children_total"] == 0


async def test_deleting_a_parent_with_children_is_refused_and_says_so() -> None:
    """Silently orphaning a subtree is the worst possible delete outcome."""
    from types import SimpleNamespace

    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    await groups.create_subgroup("backend", groups.SubgroupRequest(name="api"))
    admin = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="admin")))
    with pytest.raises(HTTPException) as excinfo:
        await groups.delete_room("community", admin)
    assert excinfo.value.status_code == 409
    detail = str(excinfo.value.detail)
    assert "backend" in detail and "cascade" in detail


async def test_a_cascade_delete_removes_the_whole_branch() -> None:
    from types import SimpleNamespace

    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    await groups.create_subgroup("community", groups.SubgroupRequest(name="backend"))
    await groups.create_subgroup("backend", groups.SubgroupRequest(name="api"))
    admin = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="admin")))
    await groups.delete_room("community", admin, cascade=True)
    tree = await groups.group_tree()
    assert tree["count"] == 0


async def test_deleting_a_leaf_still_works() -> None:
    from types import SimpleNamespace

    await groups.create_room(groups.RoomCreateRequest(name="community", members=["architect"]))
    admin = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="admin")))
    await groups.delete_room("community", admin)
    tree = await groups.group_tree()
    assert tree["count"] == 0
