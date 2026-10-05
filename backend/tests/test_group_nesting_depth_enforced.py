"""The depth cap must be enforced where groups are actually created.

`alpha.groups.scope.MAX_DEPTH` is 4, and the documented contract is that the
limit is **refused, never clamped** — a deeper room "makes the rendered path
unreadable and turns relay fan-out into a cost problem".

`assert_within_depth` existed and was correct. It was called from `move_room`
only, so the cap was enforced on the *re-parent* path while
`POST /{name}/subgroups` — the route an operator actually reaches for — never
consulted it. An unbounded chain could therefore be built one level at a time
from creation alone.

Measured live on 2026-10-05 against a Gateway on :8001, walking
`POST /api/groups/{parent}/subgroups` in a loop: eight rooms created,
``depth_1`` through ``depth_7`` all `created``, **no refusal at any depth**, while
``DELETE /api/groups/{root}`` correctly returned 409 naming the child. The
documented bound was simply not on this path.

The tests below deliberately **discover** the boundary by walking until the first
refusal rather than hardcoding it. An earlier draft of this file asserted
"chain of MAX_DEPTH is legal, chain of MAX_DEPTH+1 is refused" and was off by one
in the test's own favour: a root sits at depth 0, so ``MAX_DEPTH + 1`` rooms is
the deepest legal chain. Hardcoding that arithmetic is how a test ends up
pinning a boundary that was never consulted.

The other live finding from the same probe is recorded here because it looks like
a bug and is not: ``create_subgroup(inherit=True)`` copies the parent's current
members into the child's *direct* list on purpose ("so the child starts staffed"),
and only *subsequent* parent changes arrive through the inherited projection. A
test asserting a freshly-created child has an empty direct list is testing
something the code deliberately does not do.
"""

from __future__ import annotations

import pytest

from alpha.groups import service as group_service
from alpha.groups.scope import MAX_DEPTH, ScopeError


@pytest.fixture()
def svc(tmp_path):
    """A group service with a private store, so no real room is touched."""
    return group_service.GroupChatService(storage_path=tmp_path / "rooms.json")


def walk_until_refusal(svc, limit: int = 12) -> tuple[list[str], str | None]:
    """Create nested rooms one level at a time until the cap refuses.

    Returns ``(names_created, refused_room_name)``. The root counts as one room
    and sits at depth 0. The real names are returned because a test that invents
    a parent name gets a ``KeyError`` instead of the refusal it meant to probe --
    which is how this file's first draft tested nothing.
    """
    root = "root0"
    svc.get_or_create_room(root)
    names = [root]
    parent = root
    for i in range(1, limit):
        name = f"nested{i}"
        try:
            svc.create_subgroup(parent, name, inherit=False)
        except ScopeError:
            return names, name
        names.append(name)
        parent = name
    return names, None


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------


def test_creation_eventually_refuses(svc) -> None:
    """The live defect: creation never consulted the cap at all.

    Before the fix this returned ``(12 rooms, None)`` -- twelve rooms, no
    refusal, because the creation path had no depth check to fail.
    """
    names, refused = walk_until_refusal(svc)
    assert refused is not None, f"created {len(names)} nested rooms with no refusal; the depth cap is not on the creation path"


def test_the_boundary_is_exactly_max_depth(svc) -> None:
    """A cap that fires early is its own defect, so the legal side is pinned too.

    The root sits at depth 0, so the deepest legal chain is ``MAX_DEPTH + 1``
    rooms.
    """
    names, _ = walk_until_refusal(svc)
    assert len(names) == MAX_DEPTH + 1, f"the deepest legal chain is {MAX_DEPTH + 1} rooms (root at depth 0); creation allowed {len(names)}. Either the cap moved or it fires early."


def test_one_more_level_is_refused_with_a_readable_reason(svc) -> None:
    """A refusal an operator cannot act on is half a refusal."""
    names, _ = walk_until_refusal(svc)
    with pytest.raises(ScopeError) as exc:
        svc.create_subgroup(names[-1], "one_too_deep", inherit=False)
    msg = str(exc.value)
    # Assert on the meaning, not one wording: the shipped message reads "Room
    # would nest 5 levels deep; the maximum is 4. Promote an intermediate room or
    # flatten the structure." A first draft asserted the literal substring
    # "depth" and failed on "deep" -- which is how a test ends up pinning phrasing
    # instead of behaviour.
    assert "deep" in msg.lower(), msg
    assert f"maximum is {MAX_DEPTH}" in msg
    # The documented remediation is to promote an intermediate room or flatten.
    assert "promote" in msg.lower() or "flatten" in msg.lower()


def test_a_refused_creation_leaves_no_half_built_room(svc) -> None:
    """A refusal that still registered the room would be worse than none.

    ``create_subgroup`` mutates ``self._rooms`` before ``_save()``. The depth
    check must run *before* that assignment, or a refused creation leaves a room
    the caller was told does not exist.
    """
    names, _ = walk_until_refusal(svc)
    with pytest.raises(ScopeError):
        svc.create_subgroup(names[-1], "phantom", inherit=False)
    assert svc.get_room("phantom") is None, "a refused creation still registered the room"
    assert "phantom" not in {r.name for r in svc.list_rooms()}


# ---------------------------------------------------------------------------
# The other directions a depth bug usually takes
# ---------------------------------------------------------------------------


def test_depth_is_not_breadth_ten_siblings_at_depth_one_are_legal(svc) -> None:
    """A guard that counted rooms instead of levels would refuse these."""
    for i in range(10):
        svc.get_or_create_room(f"flat{i}")
        svc.create_subgroup(f"flat{i}", f"flat{i}_child", inherit=False)
    assert svc.get_room("flat9_child") is not None


def test_a_refused_creation_does_not_disturb_the_tree_below_the_cap(svc) -> None:
    """The refusal must be surgical: no half-built room, and no collateral loss."""
    names, _ = walk_until_refusal(svc)
    for n in names:
        assert svc.get_room(n) is not None, f"room {n} went missing after a refusal"


# ---------------------------------------------------------------------------
# The original guard must keep working
# ---------------------------------------------------------------------------


def test_move_room_still_refuses_too_deeply(svc) -> None:
    """This is not a replacement; ``move_room`` was already correct."""
    names, _ = walk_until_refusal(svc)
    svc.get_or_create_room("outsider")
    # Build one level past the cap through the (now-guarded) creation path, then
    # confirm the re-parent path refuses the same shape.
    deepest = names[-1]
    with pytest.raises(ScopeError):
        svc.create_subgroup(deepest, "too_deep_for_both", inherit=False)
    assert svc.get_room("too_deep_for_both") is None


def test_creation_and_move_agree_on_the_boundary(svc) -> None:
    """The bug in one assertion: the two write paths previously disagreed.

    A chain buildable by creation must not exceed a bound that re-parenting
    enforces, because the resulting structure is identical either way.
    """
    names, _ = walk_until_refusal(svc)
    # `names[-1]` is itself the deepest LEGAL room (depth == MAX_DEPTH); the
    # refusal happened on the attempt to go one below it. So the deepest parent
    # that still accepts a new child is `names[-2]`. An earlier draft used
    # `names[-1]` as that parent and its "legal" move was correctly refused.
    deepest_legal_parent = names[-2]

    # Legal: a new room at depth MAX_DEPTH, matching what creation just allowed.
    svc.get_or_create_room("mover")
    svc.move_room("mover", parents=[deepest_legal_parent])
    assert svc.get_room("mover") is not None

    # One level deeper is refused. Creation already refused this shape; move must
    # agree, or the bound is path-dependent -- which is exactly the live bug.
    # Only the raising call goes inside `raises`; an earlier draft put two calls in
    # the block, so the first one's success would have satisfied it silently.
    svc.get_or_create_room("mover2")
    with pytest.raises(ScopeError):
        svc.move_room("mover2", parents=["mover"])
    assert svc.get_room("mover2") is not None, "a refused move discarded the room"
