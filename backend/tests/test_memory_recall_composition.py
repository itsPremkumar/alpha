"""The central recall-composition seam.

This module is where "is the new memory type actually wired?" becomes a
mechanical question instead of a code-reading exercise. The tests here pin the
four properties that make it safe to land a whole wave of types at once:

1. every type OFF means byte-identical memory output (default-OFF is real);
2. one broken type cannot break the turn or invent replacement content;
3. the composed block is bounded, deterministic, and discloses truncation;
4. the per-surface status record accounts for every requested surface, so a
   type cannot be silently skipped.
"""

from __future__ import annotations

import pytest

from alpha.config.memory_config import MemoryConfig
from alpha.memory import recall_composition as rc


def _config(**sections: object) -> MemoryConfig:
    payload: dict[str, object] = {"enabled": True}
    for name, value in sections.items():
        payload[name] = value
    return MemoryConfig.model_validate(payload)


def test_every_type_off_produces_no_text_and_no_headings() -> None:
    """Default-OFF must be invisible, not merely empty.

    If a disabled type emitted a heading or a "disabled" notice, every
    existing deployment's prompt would change the moment this file landed.
    """
    result = rc.compose_typed_memory_blocks(MemoryConfig(), user_id="u1")
    assert result.text == ""
    assert result.truncated is False
    assert result.enabled_surfaces == ()
    assert {s.surface for s in result.surfaces} == set(rc.SURFACE_ORDER)
    assert all(s.status == rc.STATUS_DISABLED for s in result.surfaces)


def test_host_memory_switch_off_disables_all_surfaces() -> None:
    """The two-level gate: a type's own enabled flag cannot outrank the host."""
    config = MemoryConfig.model_validate(
        {"enabled": False, "affective": {"enabled": True}, "narrative": {"enabled": True}}
    )
    result = rc.compose_typed_memory_blocks(config, user_id="u1")
    assert result.text == ""
    assert all(s.status == rc.STATUS_DISABLED for s in result.surfaces)


def test_enabled_type_with_empty_store_reports_empty_not_text(tmp_path) -> None:
    """An enabled type with nothing stored contributes no text."""
    config = MemoryConfig.model_validate(
        {
            "enabled": True,
            "narrative": {"enabled": True, "storage_path": str(tmp_path / "narrative")},
        }
    )
    result = rc.compose_typed_memory_blocks(config, user_id="u1", surfaces=("narrative",))
    assert result.text == ""
    assert result.status_for("narrative") in {rc.STATUS_EMPTY, rc.STATUS_OK}
    if result.status_for("narrative") == rc.STATUS_OK:
        # A non-empty block here would mean the renderer invented content.
        pytest.fail("narrative rendered content from an empty store")


def test_a_broken_surface_is_isolated_and_disclosed(monkeypatch) -> None:
    """One raising type must not fail the turn, and must not be faked over."""

    def boom(*args: object, **kwargs: object) -> str:
        msg = "store exploded"
        raise RuntimeError(msg)

    monkeypatch.setitem(rc._RENDERERS, "affective", boom)
    config = _config(affective={"enabled": True}, narrative={"enabled": True})

    result = rc.compose_typed_memory_blocks(
        config, user_id="u1", surfaces=("affective", "narrative")
    )
    assert result.status_for("affective") == rc.STATUS_ERROR
    assert result.failed_surfaces == ("affective",)
    # The failure detail is the exception TYPE, never a rendered message that
    # could be mistaken for memory content.
    detail = next(s.detail for s in result.surfaces if s.surface == "affective")
    assert detail == "RuntimeError"
    # And no placeholder text stood in for the failed surface.
    assert "store exploded" not in result.text
    # The healthy surface was still attempted.
    assert result.status_for("narrative") in {rc.STATUS_EMPTY, rc.STATUS_OK}


def test_composed_block_is_bounded_and_truncation_is_disclosed(monkeypatch) -> None:
    """The cap bounds EVERY emitted byte, and the disclosure is not optional.

    Since the data-safety notice was added, ``max_total_chars`` is reserved
    against the notice and the join separator BEFORE any surface renders, so the
    cap covers the whole block rather than only the surface text. The original
    exact-accounting property still holds -- just against the reserved budget.
    """
    monkeypatch.setitem(rc._RENDERERS, "affective", lambda *a, **k: "A" * 500)
    monkeypatch.setitem(rc._RENDERERS, "narrative", lambda *a, **k: "B" * 500)
    config = _config(affective={"enabled": True}, narrative={"enabled": True})

    cap = 900
    result = rc.compose_typed_memory_blocks(
        config, user_id="u1", surfaces=("affective", "narrative"), max_total_chars=cap
    )
    assert result.truncated is True
    assert "truncated at the configured cap" in result.text
    # The block opens with the data notice and closes with its reminder, both
    # before any recall payload.
    assert result.text.startswith(rc.RECALL_DATA_NOTICE + "\n\n")
    assert rc._RECALL_DATA_REMINDER in result.text
    # The cap bounds CONTENT: the first block fits whole, the second is cut to
    # whatever is left after the payload AND the join.
    body = result.text.removeprefix(rc.RECALL_DATA_NOTICE + "\n\n").removesuffix(rc._TRUNCATION_NOTICE)
    body = body.removesuffix("\n\n" + rc._RECALL_DATA_REMINDER)
    budget = cap - len(rc.RECALL_DATA_NOTICE) - len(rc._RECALL_DATA_REMINDER) - 4
    assert body.count("A") == 500
    assert body.count("B") == budget - 500 - len("\n\n")
    # And the disclosure sits OUTSIDE the cap on purpose, so a truncation is
    # never silent -- while the capped content plus the notices never exceeds it.
    assert len(result.text) == len(body) + len(rc.RECALL_DATA_NOTICE) + 2 + len(rc._RECALL_DATA_REMINDER) + 2 + len(rc._TRUNCATION_NOTICE)
    assert len(rc.RECALL_DATA_NOTICE) + 2 + len(body) + 2 + len(rc._RECALL_DATA_REMINDER) <= cap


def test_surface_order_is_deterministic_across_calls(monkeypatch) -> None:
    for name in rc.SURFACE_ORDER:
        # Bind the name now: a bare closure would capture the loop variable and
        # every renderer would return the last name's block.
        monkeypatch.setitem(rc._RENDERERS, name, lambda *a, _n=name, **k: f"{_n}-block")
    config = _config(**{name: {"enabled": True} for name in rc.SURFACE_ORDER})
    first = rc.compose_typed_memory_blocks(config, user_id="u1")
    second = rc.compose_typed_memory_blocks(config, user_id="u1")
    assert first.text == second.text
    assert [s.surface for s in first.surfaces] == list(rc.SURFACE_ORDER)
    assert first.text.index("affective-block") < first.text.index("narrative-block")


def test_missing_user_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="user_id"):
        rc.compose_typed_memory_blocks(MemoryConfig(), user_id="  ")


def test_unknown_surface_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown memory surface"):
        rc.compose_typed_memory_blocks(MemoryConfig(), user_id="u1", surfaces=("nope",))


def test_l1_is_not_in_the_composed_set() -> None:
    """L1 keeps its own landed prompt path; folding it in would double-render."""
    assert "l1" not in rc.SURFACE_ORDER
    assert "l1" not in rc._RENDERERS
