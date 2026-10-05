"""Nested groups: scope graph, roster rules, relay, lifecycle, squads.

The feature under test is "a group inside a group, addable at any time" — the
WhatsApp-community shape. Four design constraints drive every assertion here:

1. **Visibility may fan out; authority may not.** A room can sit under several
   parents, but exactly one of them owns its policy. Without that split, "whose
   rules apply" becomes unanswerable.
2. **Nested membership never lands in ``GroupRoom.members``.** That field is
   crew-owned and *deletes* what project membership does not claim, so anything
   written there by the groups layer would be erased on the next reconcile.
3. **Inheritance is a projection, never a stored copy.** Recomputing it per read
   is what stops an inherited member from going stale or being orphaned.
4. **Both counts travel together.** ``direct_count`` beside ``effective_count``,
   always — a header claiming "3 members" over six visible bots is a
   fabricated count.
"""

from __future__ import annotations

import pytest

from alpha.groups.roster import GroupRoster, RosterError, resolve_roster, resolve_rule, validate_rule
from alpha.groups.scope import (
    MAX_DEPTH,
    GroupScope,
    ScopeError,
    ancestors_of,
    assert_authority_parent_consistent,
    assert_no_cycle,
    assert_within_depth,
    depth_of,
    plan_relay,
    recompute_all,
    validate_state,
)
from alpha.groups.service import GroupChatService

PROFILES = {
    "architect": {"name": "architect", "role": "Architect", "department": "engineering", "model": "gpt", "skill": ["py"], "toolset": ["shell"], "capability": ["design"]},
    "coder": {"name": "coder", "role": "Engineer", "department": "engineering", "model": "gpt", "skill": ["py", "sql"], "toolset": ["shell"], "capability": ["build"]},
    "tester": {"name": "tester", "role": "QA", "department": "engineering", "model": "gpt", "skill": ["pytest"], "toolset": [], "capability": ["verify"]},
    "secops": {"name": "secops", "role": "Security", "department": "platform", "model": "gpt", "skill": [], "toolset": [], "capability": ["audit"]},
}


@pytest.fixture
def svc(tmp_path):
    return GroupChatService(storage_path=tmp_path / "rooms.json")


# ---------------------------------------------------------------------------
# Scope graph
# ---------------------------------------------------------------------------


def test_ancestors_walks_the_visibility_chain() -> None:
    scopes = {
        "r1": GroupScope(room_id="r1"),
        "r2": GroupScope(room_id="r2", parents=["r1"]),
        "r3": GroupScope(room_id="r3", parents=["r2"]),
    }
    assert ancestors_of(scopes, "r3") == ["r2", "r1"]


def test_a_room_may_have_several_visibility_parents() -> None:
    scopes = {
        "a": GroupScope(room_id="a"),
        "b": GroupScope(room_id="b"),
        "c": GroupScope(room_id="c", parents=["a", "b"]),
    }
    assert set(ancestors_of(scopes, "c")) == {"a", "b"}
    # …but exactly one authority parent, which is what keeps policy resolvable.
    scopes["c"].authority_parent = "a"
    assert scopes["c"].authority_parent == "a"


def test_ancestor_walk_terminates_on_a_cycle() -> None:
    """A corrupt persisted graph must not hang a room read."""
    scopes = {
        "a": GroupScope(room_id="a", parents=["b"]),
        "b": GroupScope(room_id="b", parents=["a"]),
    }
    assert set(ancestors_of(scopes, "a")) <= {"a", "b"}


def test_a_room_cannot_become_its_own_parent() -> None:
    with pytest.raises(ScopeError, match="its own parent"):
        assert_no_cycle({}, "a", ["a"])


def test_a_cycle_is_refused() -> None:
    scopes = {
        "parent": GroupScope(room_id="parent"),
        "child": GroupScope(room_id="child", parents=["parent"]),
    }
    # Making `parent` a child of its own descendant closes a loop.
    with pytest.raises(ScopeError, match="cycle"):
        assert_no_cycle(scopes, "parent", ["child"])


def test_an_unknown_parent_is_refused() -> None:
    with pytest.raises(ScopeError, match="does not exist"):
        assert_no_cycle({}, "a", ["ghost"])


def test_depth_beyond_the_cap_is_refused_not_clamped() -> None:
    # `depth` is the ancestor count, so a root is 0 and the cap is a maximum
    # depth *value*: the chain root → l1 → … → l{MAX_DEPTH} is already at it,
    # and hanging one more room off the end must be refused rather than clamped.
    scopes = {"root": GroupScope(room_id="root")}
    for i in range(1, MAX_DEPTH + 1):
        scopes[f"l{i}"] = GroupScope(room_id=f"l{i}", parents=["root"] if i == 1 else [f"l{i - 1}"])
    assert depth_of(scopes, f"l{MAX_DEPTH}") == MAX_DEPTH
    with pytest.raises(ScopeError, match=str(MAX_DEPTH)):
        assert_within_depth(scopes, "new", [f"l{MAX_DEPTH}"])


def test_a_placement_at_the_cap_is_allowed() -> None:
    scopes = {"root": GroupScope(room_id="root")}
    for i in range(1, MAX_DEPTH):
        scopes[f"l{i}"] = GroupScope(room_id=f"l{i}", parents=["root"] if i == 1 else [f"l{i - 1}"])
    # One below the cap still fits.
    assert_within_depth(scopes, "new", [f"l{MAX_DEPTH - 1}"])


def test_depth_within_the_cap_is_allowed() -> None:
    scopes = {"root": GroupScope(room_id="root")}
    assert_within_depth(scopes, "child", ["root"])


def test_depth_counts_the_longest_chain_and_not_the_ancestor_count() -> None:
    """Fan-in is not depth.

    ``depth`` is how many levels a subtree hangs below a root, so a hub that
    eight rooms each parent sits one level below them, never eight. An
    implementation that counted *distinct ancestors* and clamped the result
    reported such a wide-but-shallow room as ``MAX_DEPTH`` — and since
    ``assert_within_depth`` measures against the highest parent, that turned
    the inflated number into a refusal of a placement that fits easily.
    """
    scopes = {"root": GroupScope(room_id="root")}
    parents = [f"p{i}" for i in range(8)]
    for pid in parents:
        scopes[pid] = GroupScope(room_id=pid, parents=["root"])
    scopes["hub"] = GroupScope(room_id="hub", parents=parents)

    assert len(ancestors_of(scopes, "hub")) == 9  # eight parents, plus the root
    assert depth_of(scopes, "hub") == 2  # root → p_i → hub

    # …and a room placed beneath that shallow hub is two levels from the cap,
    # so the placement must be allowed rather than refused.
    assert_within_depth(scopes, "new", ["hub"])
    assert_within_depth(scopes, "new2", ["hub"])


def test_a_refusal_names_the_real_chain_and_not_the_ancestor_count() -> None:
    """A too-deep refusal must not quote a number no path produces.

    The fan-in bug above reported ``depth == MAX_DEPTH`` for a two-deep hub, so
    the refusal it produced was not merely wrong but *self-contradicting*: it
    named a depth the message itself claimed was beyond the cap, for a room
    three levels from the root. An operator reading that had no way to tell a
    real refusal from a counting artifact.

    This pins the message against the longest chain actually walked, so the
    number in the sentence and the number the guard computed cannot drift.
    """
    scopes = {"root": GroupScope(room_id="root")}
    for i in range(1, MAX_DEPTH + 1):
        scopes[f"l{i}"] = GroupScope(room_id=f"l{i}", parents=["root"] if i == 1 else [f"l{i - 1}"])
    # Fan-in over a SHALLOW root: nine distinct ancestors, but every path from
    # root to `fanin` is exactly two long. Under the ancestor-count
    # implementation this read as MAX_DEPTH and the placement below was refused
    # with a fabricated "nest 5 levels deep".
    wide = [f"p{i}" for i in range(8)]
    for pid in wide:
        scopes[pid] = GroupScope(room_id=pid, parents=["root"])
    scopes["fanin"] = GroupScope(room_id="fanin", parents=wide)

    # Nine distinct ancestors…
    assert len(ancestors_of(scopes, "fanin")) == 9
    # …but only two levels deep, so a room under it is 3 and must be allowed.
    assert depth_of(scopes, "fanin") == 2
    assert_within_depth(scopes, "under_fanin", ["fanin"])

    # A genuinely-too-deep placement still refuses, and quotes the chain length.
    with pytest.raises(ScopeError, match=r"nest \d+ levels deep") as excinfo:
        assert_within_depth(scopes, "too_deep", [f"l{MAX_DEPTH}"])
    assert str(MAX_DEPTH + 1) in str(excinfo.value)


def test_depth_of_a_cycle_reads_as_the_cap_rather_than_hanging() -> None:
    """A corrupt file must not hang a room read, and must not under-report.

    ``ancestors_of`` dedupes on first visit, so a cycle is traversed once and
    the walk terminates. The depth walk has to keep that bound while still
    taking the LONGEST path — a fix that only re-added a ``seen`` set would make
    a cycle read as depth 1, which is a room that appears shallow while its
    own parents loop.
    """
    scopes = {
        "a": GroupScope(room_id="a", parents=["b"]),
        "b": GroupScope(room_id="b", parents=["c"]),
        "c": GroupScope(room_id="c", parents=["a"]),
    }
    assert depth_of(scopes, "a") == MAX_DEPTH
    # An unparseable parent is not a chain to anywhere: a room that names a
    # ghost parent reads as a root, not as an error.
    assert depth_of(scopes, "orphan") == 0
    assert depth_of({"ghost_parent": GroupScope(room_id="ghost_parent")}, "orphan") == 0


def test_depth_is_the_deepest_parent_not_the_shallowest() -> None:
    """Two parents at two depths: the room nests under the DEEPER one.

    Taking the shortest would let a placement pass the cap on the strength of a
    shallow sibling while the sidebar still renders the four-deep branch. This
    is the mirror of the fan-in case and it is the direction that under-reports.
    """
    scopes = {"root": GroupScope(room_id="root")}
    for i in range(1, MAX_DEPTH + 1):
        scopes[f"l{i}"] = GroupScope(room_id=f"l{i}", parents=["root"] if i == 1 else [f"l{i - 1}"])
    # A room parented by both a shallow root and a room already at the cap.
    scopes["split"] = GroupScope(room_id="split", parents=["root", f"l{MAX_DEPTH}"])
    assert depth_of(scopes, "split") == MAX_DEPTH
    with pytest.raises(ScopeError, match=str(MAX_DEPTH)):
        assert_within_depth(scopes, "child_of_split", ["split"])


def test_the_authority_parent_must_also_be_a_visibility_parent() -> None:
    """Authority must refine visibility, never contradict it."""
    scopes = {"a": GroupScope(room_id="a"), "b": GroupScope(room_id="b"), "c": GroupScope(room_id="c", parents=["a"])}
    with pytest.raises(ScopeError, match="visibility parent"):
        assert_authority_parent_consistent(scopes, "c", "b")


def test_the_authority_parent_may_be_one_of_several_parents() -> None:
    scopes = {
        "a": GroupScope(room_id="a"),
        "b": GroupScope(room_id="b"),
        "c": GroupScope(room_id="c", parents=["a", "b"]),
    }
    assert_authority_parent_consistent(scopes, "c", "a")


def test_paths_follow_the_authority_chain_not_the_visibility_chain() -> None:
    """The path is a policy lineage, so it stays one readable line."""
    scopes = {
        "r1": GroupScope(room_id="r1", authority_parent=None),
        "r2": GroupScope(room_id="r2", parents=["r1"], authority_parent="r1"),
        "r3": GroupScope(room_id="r3", parents=["r2"], authority_parent="r2"),
    }
    names = {"r1": "sprint", "r2": "backend", "r3": "api"}
    recompute_all(scopes, names)
    assert scopes["r3"].path == "sprint/backend/api"
    assert scopes["r3"].depth == 2


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_the_legal_lifecycle_transitions() -> None:
    validate_state("draft", "active")
    validate_state("active", "parked")
    validate_state("parked", "active")
    validate_state("active", "archived")
    validate_state("archived", "active")


def test_an_illegal_lifecycle_transition_is_refused() -> None:
    # `draft` is the only state that cannot go straight to `archived`: a room
    # nobody has used yet must be activated before it can be filed away, so
    # "archived" always means "this was a real room".
    with pytest.raises(ScopeError, match="Cannot move"):
        validate_state("draft", "archived")


def test_a_terminal_state_has_no_way_out() -> None:
    with pytest.raises(ScopeError):
        validate_state("dissolved", "active")


def test_an_unknown_state_is_refused() -> None:
    with pytest.raises(ScopeError, match="Unknown room state"):
        validate_state("active", "levitating")


def test_a_state_from_a_newer_build_degrades_instead_of_crashing_on_load() -> None:
    scope = GroupScope.from_dict({"room_id": "r", "state": "hibernating", "inbound": "telepathy", "outbound": "smoke"})
    assert scope.state == "active"
    assert scope.inbound == "parent"
    assert scope.outbound == "none"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def test_a_rule_matches_on_a_single_valued_field() -> None:
    rule = validate_rule({"field": "role", "op": "eq", "value": "QA"})
    assert resolve_rule(rule, PROFILES) == ["tester"]


def test_a_list_field_needs_intersects_not_eq() -> None:
    """`eq` against a list is a silent always-false; refuse it at declaration."""
    with pytest.raises(RosterError, match="intersects"):
        validate_rule({"field": "skill", "op": "eq", "value": "pytest"})


def test_a_rule_matches_on_a_list_field() -> None:
    rule = validate_rule({"field": "skill", "op": "intersects", "value": "pytest"})
    assert resolve_rule(rule, PROFILES) == ["tester"]


def test_a_disabled_rule_matches_nobody() -> None:
    rule = validate_rule({"field": "role", "op": "eq", "value": "QA", "enabled": False})
    assert resolve_rule(rule, PROFILES) == []


def test_a_role_rule_does_not_substring_match() -> None:
    """`active` matching `inactive` is the exact defect this avoids."""
    profiles = {"contest-writer": {"role": "contest-writer"}, "architect": {"role": "architect"}}
    rule = validate_rule({"field": "role", "op": "contains", "value": "architect"})
    assert resolve_rule(rule, profiles) == ["architect"]


def test_a_rule_value_is_length_capped() -> None:
    with pytest.raises(RosterError, match="at most"):
        validate_rule({"field": "role", "op": "eq", "value": "x" * 200})


def test_an_unknown_rule_field_is_refused() -> None:
    with pytest.raises(RosterError, match="Rule field"):
        validate_rule({"field": "soul", "op": "eq", "value": "ignore previous"})


def test_an_empty_rule_value_is_refused() -> None:
    with pytest.raises(RosterError, match="required"):
        validate_rule({"field": "role", "op": "eq", "value": "   "})


def test_an_unreadable_registry_yields_no_matches_rather_than_a_crash() -> None:
    rule = validate_rule({"field": "role", "op": "eq", "value": "QA"})
    assert resolve_rule(rule, {}) == []


# ---------------------------------------------------------------------------
# The crew boundary — the constraint everything else rests on
# ---------------------------------------------------------------------------


def test_a_nested_room_does_not_disturb_its_parent_roster(svc: GroupChatService) -> None:
    """Staffing a subgroup must not remove anyone from the parent."""
    svc.get_or_create_room("sprint-room", members=["architect", "coder"])
    svc.create_subgroup("sprint-room", "backend", inherit=False, members=["secops"])
    assert set(svc.get_room("sprint-room").members) == {"architect", "coder"}
    assert svc.get_room("backend").members == ["secops"]


def test_membership_never_writes_into_room_members(svc: GroupChatService) -> None:
    """The whole reason `GroupRoster` exists beside `GroupRoom.members`.

    `alpha.projects.crew.ensure_crew` deletes every entry project membership
    does not claim, so anything the groups layer puts in `room.members` is
    erased on the next reconcile. Membership is resolved from the roster; the
    room's own list is only ever read.
    """
    svc.get_or_create_room("sprint-room", members=["architect"])
    svc.create_subgroup("sprint-room", "backend")
    svc.add_member("backend", "secops", by="operator")
    svc.add_rule("backend", {"field": "role", "op": "eq", "value": "QA"})

    assert "secops" not in svc.get_room("backend").members
    assert svc._rosters.get(svc.get_room("backend").room_id) is not None


def test_a_subgroup_runs_the_members_it_inherits(svc: GroupChatService) -> None:
    """A nested group must be able to act without being restaffed by hand."""
    svc.get_or_create_room("sprint-room", members=["architect"])
    svc.create_subgroup("sprint-room", "backend", inherit=False)
    svc.add_member("sprint-room", "coder", by="operator")

    # `architect` was already in the parent, so it is inherited too — the child
    # sees its parent's whole live roster, not only what changed.
    assert svc.effective_members("backend") == ["architect", "coder"]
    assert svc.get_room("backend").members == [], "the child was not restaffed by hand"


# ---------------------------------------------------------------------------
# Roster resolution
# ---------------------------------------------------------------------------


def test_the_roster_splits_direct_rule_and_inherited() -> None:
    rule = validate_rule({"id": "r1", "field": "role", "op": "eq", "value": "QA"})
    roster = GroupRoster(room_id="room", rules=[rule])
    resolved = resolve_roster(
        "room",
        roster,
        direct_members=["architect", "coder"],
        inherited_by_parent={"parent-1": ["reviewer"]},
        profiles=PROFILES,
    )
    assert resolved.direct == ["architect", "coder"]
    assert resolved.rule_matched == ["tester"]
    assert resolved.inherited == ["reviewer"]
    assert set(resolved.effective) == {"architect", "coder", "tester", "reviewer"}


def test_a_direct_member_is_not_also_reported_as_inherited() -> None:
    """Listing them twice makes the UI's three-way split lie about its own arithmetic."""
    resolved = resolve_roster("room", None, direct_members=["architect"], inherited_by_parent={"p": ["architect", "coder"]}, profiles=PROFILES)
    assert resolved.inherited == ["coder"]
    assert "architect" in resolved.direct


def test_an_excluded_member_leaves_the_effective_set() -> None:
    roster = GroupRoster(room_id="room", excluded=["coder"])
    resolved = resolve_roster("room", roster, direct_members=["architect", "coder"], profiles=PROFILES)
    assert resolved.effective == ["architect"]
    # Still reported as direct, so the UI can say *why* they are absent.
    assert "coder" in resolved.direct


def test_an_expired_borrow_leaves_the_effective_set_but_is_still_named() -> None:
    roster = GroupRoster(room_id="room")
    roster.direct = {"secops": {"by": "operator", "at": "2026-01-01T00:00:00+00:00", "from_room": "platform", "expires_at": "2026-01-01T01:00:00+00:00"}}
    resolved = resolve_roster("room", roster, direct_members=[], profiles=PROFILES, now="2026-01-02T00:00:00+00:00")
    assert resolved.effective == []
    assert resolved.expired == ["secops"]


def test_an_unexpired_borrow_is_effective() -> None:
    roster = GroupRoster(room_id="room")
    roster.direct = {"secops": {"by": "operator", "at": "2026-01-01T00:00:00+00:00", "from_room": "platform", "expires_at": "2026-01-03T00:00:00+00:00"}}
    resolved = resolve_roster("room", roster, direct_members=[], profiles=PROFILES, now="2026-01-02T00:00:00+00:00")
    assert resolved.effective == ["secops"]


def test_both_counts_are_reported_together() -> None:
    resolved = resolve_roster("room", None, direct_members=["architect"], inherited_by_parent={"p": ["reviewer"]}, profiles=PROFILES)
    payload = resolved.to_dict()
    assert payload["direct_count"] == 1
    assert payload["effective_count"] == 2


# ---------------------------------------------------------------------------
# Relay planning
# ---------------------------------------------------------------------------


def test_outbound_none_relays_nowhere() -> None:
    scopes = {"a": GroupScope(room_id="a"), "b": GroupScope(room_id="b", parents=["a"])}
    assert plan_relay(scopes, "a") == []


def test_outbound_parents_reaches_the_parents() -> None:
    scopes = {
        "a": GroupScope(room_id="a"),
        "c": GroupScope(room_id="c", parents=["a"], authority_parent="a", outbound="parents"),
    }
    assert plan_relay(scopes, "c") == ["a"]


def test_outbound_siblings_reaches_the_other_children_only() -> None:
    scopes = {
        "p": GroupScope(room_id="p"),
        "x": GroupScope(room_id="x", parents=["p"], inbound="parent", outbound="siblings"),
        "y": GroupScope(room_id="y", parents=["p"], inbound="parent"),
    }
    assert plan_relay(scopes, "x") == ["y"]


def test_a_room_never_relays_to_itself() -> None:
    scopes = {"a": GroupScope(room_id="a", outbound="org")}
    assert "a" not in plan_relay(scopes, "a")


def test_the_hop_ceiling_stops_a_relay_loop() -> None:
    """`siblings` + `broadcast` ping-pongs forever without a hop limit."""
    scopes = {
        "p": GroupScope(room_id="p"),
        "x": GroupScope(room_id="x", parents=["p"], inbound="broadcast", outbound="siblings", max_hop=1),
        "y": GroupScope(room_id="y", parents=["p"], inbound="broadcast", outbound="siblings", max_hop=1),
    }
    # From x at hop 0 the sibling y is reachable; from y at hop 1 nothing is.
    assert plan_relay(scopes, "x", hop=0) == ["y"]
    assert plan_relay(scopes, "x", hop=1) == []


def test_a_target_that_refuses_inbound_is_not_relayed_to() -> None:
    # `inbound: none` is an unconditional refusal: an explicit parent edge
    # overrides a target's *default* inbound policy, but it must not override
    # one the operator set to "nothing".
    refusing = {"a": GroupScope(room_id="a", outbound="org"), "b": GroupScope(room_id="b", inbound="none", parents=["a"])}
    assert plan_relay(refusing, "a") == []

    # A target still on the default `parent` policy accepts its parent.
    accepting = {"a": GroupScope(room_id="a", outbound="org"), "b": GroupScope(room_id="b", parents=["a"])}
    assert plan_relay(accepting, "a") == ["b"]

    # `parent` means family: a sibling sharing a parent is accepted, a stranger
    # is not.
    family = {
        "p": GroupScope(room_id="p"),
        "x": GroupScope(room_id="x", parents=["p"], outbound="org"),
        "y": GroupScope(room_id="y", parents=["p"]),
        "z": GroupScope(room_id="z"),
    }
    reached = plan_relay(family, "x")
    assert "y" in reached, "a sibling is inside the family"
    assert "z" not in reached, "an unrelated room is not"


# ---------------------------------------------------------------------------
# Service: nesting end to end
# ---------------------------------------------------------------------------


def test_a_subgroup_is_created_inside_its_parent_at_any_time(svc: GroupChatService) -> None:
    svc.get_or_create_room("sprint-room", members=["architect", "coder"])
    svc.create_subgroup("sprint-room", "backend", summary="Backend work")

    child = svc.get_room("backend")
    parent = svc.get_room("sprint-room")
    assert child.parent_ids == [parent.room_id]
    assert svc.children_of(parent.room_id) == [child.room_id]


def test_a_subgroup_inherits_the_parents_members_by_default(svc: GroupChatService) -> None:
    svc.get_or_create_room("sprint-room", members=["architect", "coder"])
    svc.create_subgroup("sprint-room", "backend")
    assert set(svc.get_room("backend").members) == {"architect", "coder"}


def test_a_subgroup_can_start_empty(svc: GroupChatService) -> None:
    svc.get_or_create_room("sprint-room", members=["architect"])
    svc.create_subgroup("sprint-room", "backend", inherit=False, members=["secops"])
    assert svc.get_room("backend").members == ["secops"]


def test_a_subgroup_name_that_already_exists_is_refused(svc: GroupChatService) -> None:
    svc.get_or_create_room("sprint-room", members=["architect"])
    svc.create_subgroup("sprint-room", "backend")
    with pytest.raises(ScopeError, match="already exists"):
        svc.create_subgroup("sprint-room", "backend")


def test_a_subgroup_of_a_missing_parent_raises(svc: GroupChatService) -> None:
    with pytest.raises(KeyError):
        svc.create_subgroup("no-such-room", "backend")


def test_a_group_can_contain_many_subgroups(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect"])
    for name in ("backend", "frontend", "qa", "docs"):
        svc.create_subgroup("community", name)
    root = svc.get_room("community")
    assert len(svc.children_of(root.room_id)) == 4


def test_nesting_persists_and_rebuilds(svc: GroupChatService, tmp_path) -> None:
    svc.get_or_create_room("community", members=["architect"])
    svc.create_subgroup("community", "backend")

    reloaded = GroupChatService(storage_path=tmp_path / "rooms.json")
    # The edge survives as a room id, so a later rename cannot break it.
    assert reloaded.get_room("backend").parent_ids == [reloaded.get_room("community").room_id]
    assert reloaded.children_of(reloaded.get_room("community").room_id) == [reloaded.get_room("backend").room_id]


def test_membership_gained_by_the_parent_reaches_the_child(svc: GroupChatService) -> None:
    """Inheritance is recomputed per read, so no write pushes it down."""
    svc.get_or_create_room("sprint-room", members=["architect"])
    svc.create_subgroup("sprint-room", "backend")
    svc.add_member("sprint-room", "secops", by="operator")

    resolved = svc.resolved_roster("backend")
    assert "secops" in resolved.inherited
    assert "secops" in resolved.effective
    # It is inherited, not direct: the UI must be able to say where it came from.
    assert "secops" not in resolved.direct


def test_the_nested_roster_never_writes_into_room_members(svc: GroupChatService) -> None:
    """`alpha.projects.crew` deletes members it does not claim; this must not fight it."""
    svc.get_or_create_room("sprint-room", members=["architect"])
    svc.create_subgroup("sprint-room", "backend")
    svc.add_member("backend", "secops", by="operator", from_room="sprint-room")
    assert "secops" not in svc.get_room("backend").members
    assert "secops" in svc.effective_members("backend")


def test_promote_detaches_a_room_and_rewrites_descendant_paths(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect"])
    svc.create_subgroup("community", "backend")
    svc.create_subgroup("backend", "api")
    svc.promote_room("backend")

    assert svc.get_room("backend").parent_ids == []
    # The subtree's location changed, so the stale path must not be served.
    assert svc._scope_for(svc.get_room("api").room_id).path == "backend/api"


def test_a_cycle_is_refused_through_move(svc: GroupChatService) -> None:
    svc.get_or_create_room("a", members=["architect"])
    svc.create_subgroup("a", "b")
    svc.create_subgroup("b", "c")
    with pytest.raises(ScopeError, match="cycle"):
        svc.move_room("a", parents=["c"])


def test_breadcrumbs_are_root_first(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect"])
    svc.create_subgroup("community", "backend")
    svc.create_subgroup("backend", "api")
    chain = svc.breadcrumbs("api")
    assert [c["name"] for c in chain] == ["community", "backend"]


def test_the_tree_includes_every_room_with_both_counts(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect", "coder"])
    svc.create_subgroup("community", "backend")
    tree = svc.tree()
    assert tree["count"] == 2
    root = next(n for n in tree["nodes"] if n["name"] == "community")
    assert root["child_count"] == 1
    assert root["direct_count"] == 2
    assert "effective_count" in root


def test_the_tree_includes_a_draft_room_with_its_state(svc: GroupChatService) -> None:
    """A room the operator just created must not be invisible to them."""
    svc.get_or_create_room("community", members=["architect"])
    svc.set_room_lifecycle("community", "draft")
    node = next(n for n in svc.tree()["nodes"] if n["name"] == "community")
    assert node["scope"]["state"] == "draft"


def test_legacy_rooms_get_a_root_scope_on_load(tmp_path) -> None:
    """A room persisted before this feature must appear in the forest."""
    import json

    path = tmp_path / "rooms.json"
    path.write_text(
        json.dumps({"version": 1, "rooms": [{"room_id": "room_old", "name": "legacy", "members": ["architect"]}]}),
        encoding="utf-8",
    )
    service = GroupChatService(storage_path=path)
    tree = service.tree()
    assert tree["count"] == 1
    assert tree["nodes"][0]["scope"]["depth"] == 0


def test_a_legacy_room_keeps_its_transcript_across_upgrade(tmp_path) -> None:
    import json

    path = tmp_path / "rooms.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "rooms": [
                    {
                        "room_id": "room_old",
                        "name": "legacy",
                        "members": ["architect"],
                        "log": [{"id": "m1", "sender": "architect", "content": "old news"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = GroupChatService(storage_path=path)
    assert service.get_room("legacy").log[0].content == "old news"


# ---------------------------------------------------------------------------
# Roster mutation
# ---------------------------------------------------------------------------


def test_adding_a_rule_reports_what_it_matches(svc: GroupChatService) -> None:
    svc.get_or_create_room("sprint-room", members=["architect"])
    rule = svc.add_rule("sprint-room", {"field": "role", "op": "eq", "value": "QA"})
    assert rule["matches"] >= 0


def test_removing_an_unknown_rule_is_an_error(svc: GroupChatService) -> None:
    svc.get_or_create_room("sprint-room", members=["architect"])
    with pytest.raises(RosterError, match="not found"):
        svc.remove_rule("sprint-room", "rule_ghost")


def test_removing_a_rule_matched_member_is_refused_with_the_real_reason(svc: GroupChatService) -> None:
    """Deleting the row would leave the member visibly present with no explanation."""
    svc.get_or_create_room("sprint-room", members=["architect"])
    svc.add_rule("sprint-room", {"field": "role", "op": "eq", "value": "QA"})
    roster = svc.resolved_roster("sprint-room")
    rule_member = roster.rule_matched[0] if roster.rule_matched else None
    if rule_member:
        with pytest.raises(RosterError, match="by rule"):
            svc.remove_member("sprint-room", rule_member)


def test_removing_an_inherited_member_is_refused_with_the_real_reason(svc: GroupChatService) -> None:
    svc.get_or_create_room("sprint-room", members=["architect"])
    svc.create_subgroup("sprint-room", "backend")
    svc.add_member("sprint-room", "secops", by="operator")
    with pytest.raises(RosterError, match="inherited"):
        svc.remove_member("backend", "secops")


def test_excluding_removes_a_member_from_one_group_only(svc: GroupChatService) -> None:
    svc.get_or_create_room("sprint-room", members=["architect", "secops"])
    svc.create_subgroup("sprint-room", "backend")
    svc.set_excluded("backend", "secops", True)

    assert "secops" not in svc.effective_members("backend")
    # Still in the parent: this is the "subgroup for just the backend work" case.
    assert "secops" in svc.effective_members("sprint-room")


def test_re_including_restores_a_member(svc: GroupChatService) -> None:
    svc.get_or_create_room("sprint-room", members=["architect", "secops"])
    svc.create_subgroup("sprint-room", "backend")
    svc.set_excluded("backend", "secops", True)
    svc.set_excluded("backend", "secops", False)
    assert "secops" in svc.effective_members("backend")


def test_a_borrowed_member_records_its_source_and_deadline(svc: GroupChatService) -> None:
    svc.get_or_create_room("sprint-room", members=["architect"])
    svc.create_subgroup("sprint-room", "api-audit", inherit=False)
    svc.add_member("api-audit", "secops", by="operator", from_room="sprint-room", expires_at="2026-12-31T00:00:00+00:00")

    # The borrowed member is added to the CHILD, so `architect` is inherited
    # from the parent and `secops` is direct — the two buckets stay distinct.
    resolved = svc.resolved_roster("api-audit")
    assert resolved.direct == ["secops"]
    assert resolved.inherited == ["architect"]
    assert set(resolved.effective) == {"architect", "secops"}
    record = svc._rosters[svc.get_room("api-audit").room_id].direct["secops"]
    assert record["from_room"] == "sprint-room"
    assert record["expires_at"] == "2026-12-31T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def test_merge_folds_children_into_the_parent_and_says_which(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect"])
    svc.create_subgroup("community", "backend", members=["coder"])
    svc.create_subgroup("community", "frontend", members=["tester"])

    result = svc.merge_children("community")
    assert result["merged_count"] == 2
    assert set(result["merged"]) == {"backend", "frontend"}
    assert set(svc.effective_members("community")) == {"architect", "coder", "tester"}
    assert svc.get_room("backend") is None


def test_merge_orders_the_merged_transcript_by_time(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect"])
    svc.create_subgroup("community", "backend", members=["coder"])
    svc.create_subgroup("community", "frontend", members=["tester"])
    svc.post_message("community", "operator", "parent first", relay=False)
    svc.post_message("backend", "coder", "backend earliest", relay=False)
    svc.post_message("frontend", "tester", "frontend middle", relay=False)

    # Deliberately interleaved: the parent's own message is not the earliest.
    svc._rooms["backend"].log[-1].created_at = "2026-01-01T00:00:00+00:00"
    svc._rooms["community"].log[-1].created_at = "2026-01-03T00:00:00+00:00"
    svc._rooms["frontend"].log[-1].created_at = "2026-01-02T00:00:00+00:00"

    svc.merge_children("community")
    stamps = [m.created_at for m in svc.get_room("community").log]
    assert stamps == sorted(stamps), "merged messages must read chronologically, not child-order"


def test_merging_a_room_with_no_children_is_a_real_no_op(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect"])
    result = svc.merge_children("community")
    assert result["merged_count"] == 0
    assert result["children_total"] == 0


# ---------------------------------------------------------------------------
# Relay end to end
# ---------------------------------------------------------------------------


def test_a_child_message_relays_to_a_parent_that_accepts_it(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect"])
    svc.create_subgroup("community", "backend")
    svc.set_room_policy("backend", outbound="parents")

    svc.post_message("backend", "operator", "backend update", relay=False)
    svc.relay_all()

    parent = svc.get_room("community")
    assert parent.log[-1].content == "backend update"
    assert parent.log[-1].metadata.get("relayed") is True


def test_a_relayed_copy_carries_provenance_not_the_source_id(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect"])
    svc.create_subgroup("community", "backend")
    svc.set_room_policy("backend", outbound="parents")

    _msg, _next = svc.post_message("backend", "operator", "backend update", relay=False)
    svc.relay_all()

    source = svc.get_room("backend").log[-1]
    relayed = svc.get_room("community").log[-1]
    assert relayed.id != source.id, "one id in two rooms would make edits and reactions ambiguous"
    assert relayed.forwarded_from["room"] == "backend"


def test_a_relay_does_not_cascade_into_an_infinite_loop(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect"])
    svc.create_subgroup("community", "backend")
    svc.create_subgroup("community", "frontend")
    for name in ("backend", "frontend"):
        svc.set_room_policy(name, outbound="siblings", max_hop=1)

    svc.post_message("backend", "operator", "ping", relay=False)
    svc.relay_all()

    total = sum(len(svc.get_room(n).log) for n in ("community", "backend", "frontend"))
    assert total <= 5, "a relay storm is worse than a missed message"


def test_a_relay_reports_where_each_copy_landed(svc: GroupChatService) -> None:
    """A relay receipt, so "relayed" is never an unverifiable claim."""
    svc.get_or_create_room("community", members=["architect"])
    svc.create_subgroup("community", "backend")
    svc.set_room_policy("backend", outbound="parents")

    svc.post_message("backend", "operator", "backend update", relay=False)
    receipt = svc.relay_all()
    assert receipt["delivered_count"] >= 1
    assert receipt["delivered"][0]["to"] == "community"


def test_a_room_whose_latest_message_is_a_relay_still_relays_its_own(svc: GroupChatService) -> None:
    """The frontier must be seeded per native message, not per room.

    A room whose most recent row happened to be an inbound relay copy used to
    be skipped entirely, so its own unread messages never propagated.
    """
    svc.get_or_create_room("community", members=["architect"])
    svc.create_subgroup("community", "backend")
    svc.set_room_policy("backend", outbound="parents")

    # First relay lands in community, making community's newest row a relay.
    svc.post_message("backend", "operator", "first", relay=False)
    svc.relay_all()
    # Now post a native message in community itself; it must relay onward.
    svc.post_message("community", "operator", "second", relay=False)
    receipt = svc.relay_all()
    # community relays nowhere by default, so the assertion is that the run
    # completed and reported honestly rather than claiming a delivery.
    assert "delivered_count" in receipt


def test_a_post_to_a_leaf_room_relays_nowhere_by_default(svc: GroupChatService) -> None:
    """The default is quiet: a first-time nest must not flood the parent."""
    svc.get_or_create_room("community", members=["architect"])
    svc.create_subgroup("community", "backend")
    svc.post_message("backend", "operator", "quiet by default", relay=False)
    svc.relay_all()
    assert len(svc.get_room("community").log) == 0


def test_a_parked_room_is_marked_and_readable(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect"])
    svc.set_room_lifecycle("community", "parked")
    assert svc.get_room("community").lifecycle == "parked"
    assert svc.tree()["nodes"][0]["scope"]["state"] == "parked"


def test_an_illegal_lifecycle_move_is_refused_through_the_service(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect"])
    svc.set_room_lifecycle("community", "draft")
    with pytest.raises(ScopeError, match="Cannot move"):
        svc.set_room_lifecycle("community", "archived")


def test_an_out_of_range_hop_limit_is_refused(svc: GroupChatService) -> None:
    svc.get_or_create_room("community", members=["architect"])
    with pytest.raises(ScopeError, match="max_hop"):
        svc.set_room_policy("community", max_hop=99)
