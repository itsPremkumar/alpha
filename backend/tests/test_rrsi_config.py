"""RRSI regularizer configuration: Table 5 pins, refusal-not-clamp, the K vocabulary.

Every number asserted here is a *claim pin*. The hyperparameters come from
Table 5 of *RRSI: Regularized Recursive Self-Improvement of Agent Harnesses*
(arXiv:2609.24972) and the component vocabulary from that paper's editable set
``K``. A preset that is silently re-tuned stops being "the paper's setting"
while still carrying a provenance string that says it is, so the test — not a
comment — is what holds the claim.

House rules exercised here:

* **Bounds are refused, never clamped** — a typo must be visible, not absorbed
  into a different regularizer that reports the typo's field name back as if it
  had been honoured.
* **A bool is not a number** — ``True`` is not a score of ``1`` and not an
  integer round count.
* **Provenance is per-preset and says which values are the paper's and which
  are local** — `PRESET_SOURCES` is asserted to exist for every preset, not
  that it exists at all.
"""

from __future__ import annotations

import pytest

from alpha.rsi.generator import EVOLVABLE_SURFACES
from alpha.rsi.rrsi import (
    COMPONENTS,
    DOMAIN_PRESETS,
    PRESET_SOURCES,
    STRUCTURAL_COMPONENTS,
    SURFACE_COMPONENTS,
    RrsiParams,
    component_for,
    components_for,
    novelty,
    novelty_breakdown,
    preset,
    preset_names,
    validate_component,
)

#: Table 5 of arXiv:2609.24972, verbatim. Field names are this module's
#: ``RrsiParams`` names; the paper's symbols are ``(T, k, δ, b_min, b_max, w,
#: m_draft, n_prune, β₀, β₁)`` in that order.
TABLE5: dict[str, dict[str, float | int]] = {
    "coding": {"rounds": 20, "trials_per_task": 2, "noise_delta": 0.017, "budget_min": 1, "budget_max": 4, "stall_window": 3, "exploratory_slots": 1, "prune_window": 4, "cost_base": 0.10, "cost_slope": 44.5},
    "workspace": {"rounds": 20, "trials_per_task": 2, "noise_delta": 0.004, "budget_min": 1, "budget_max": 3, "stall_window": 3, "exploratory_slots": 1, "prune_window": 4, "cost_base": 0.10, "cost_slope": 35.4},
    "engineering": {"rounds": 40, "trials_per_task": 4, "noise_delta": 0.020, "budget_min": 1, "budget_max": 4, "stall_window": 3, "exploratory_slots": 1, "prune_window": 5, "cost_base": 0.15, "cost_slope": 24.4},
}


# --- Table 5 -----------------------------------------------------------------


def test_preset_names_match_the_papers_three_domains():
    assert preset_names() == ("coding", "workspace", "engineering")
    assert set(DOMAIN_PRESETS) == set(TABLE5)


@pytest.mark.parametrize("name", ["coding", "workspace", "engineering"])
def test_table5_values_are_pinned_verbatim(name):
    actual = preset(name).to_dict()
    for field, expected in TABLE5[name].items():
        assert actual[field] == expected, f"{name}.{field}: Table 5 reports {expected!r}, the preset carries {actual[field]!r}"


def test_the_coding_within_band_weight_is_the_reported_zero():
    """The paper reports ``w_s = 0`` for coding and ``w_s > 0`` elsewhere.

    With ``w_s = 0`` a score gain inside the noise band earns no admissibility
    credit, which is the whole point of the coding instance: only cheaper
    inference and structural novelty can buy admission there.
    """
    assert preset("coding").weight_score == 0.0
    assert preset("workspace").weight_score > 0.0
    assert preset("engineering").weight_score > 0.0


def test_every_preset_carries_per_field_provenance():
    assert set(PRESET_SOURCES) == set(DOMAIN_PRESETS)
    for name, source in PRESET_SOURCES.items():
        assert "arXiv:2609.24972" in source, f"{name} does not cite the paper"
        assert "Table 5" in source, f"{name} does not name the table its values came from"
        assert "local starting points" in source, f"{name} does not distinguish its local values from the paper's"


# --- refusal, never clamping -------------------------------------------------


def test_a_round_count_below_one_is_refused():
    with pytest.raises(ValueError, match="'rounds'"):
        RrsiParams(rounds=0).validate()


def test_a_budget_max_below_budget_min_is_refused():
    with pytest.raises(ValueError, match="'budget_max'"):
        RrsiParams(budget_min=5, budget_max=3).validate()


def test_a_negative_exploratory_slot_count_is_refused():
    with pytest.raises(ValueError, match="'exploratory_slots'"):
        RrsiParams(exploratory_slots=-1).validate()


def test_an_all_zero_weight_set_is_refused():
    with pytest.raises(ValueError, match="'weight_\\*'"):
        RrsiParams(weight_score=0.0, weight_cost=0.0, weight_novelty=0.0).validate()


def test_a_bool_is_not_a_number_or_an_int():
    """``True`` must not satisfy a bound — it would silently mean ``rounds=1``."""
    with pytest.raises(ValueError, match="'rounds'"):
        RrsiParams(rounds=True).validate()
    with pytest.raises(ValueError, match="'noise_delta'"):
        RrsiParams(noise_delta=True).validate()


def test_an_unknown_override_is_refused():
    with pytest.raises(ValueError, match="unknown RRSI hyperparameter"):
        RrsiParams().with_overrides(noise_band=0.1)


def test_an_unknown_preset_name_is_refused_rather_than_falling_back():
    with pytest.raises(ValueError, match="unknown RRSI preset"):
        preset("coding-2")
    with pytest.raises(ValueError, match="non-empty string"):
        preset("")


def test_defaults_are_the_workspace_column():
    defaults = RrsiParams()
    assert defaults.to_dict() == DOMAIN_PRESETS["workspace"].to_dict()
    defaults.validate()


# --- the component vocabulary K ---------------------------------------------


def test_K_is_the_papers_nine_editable_components():
    assert COMPONENTS == (
        "prompt",
        "control_flow",
        "config",
        "output_plumbing",
        "context_mgmt",
        "client_tool",
        "skill",
        "memory",
        "subagent",
    )


def test_structural_subset_is_declared_and_a_real_subset():
    """``K_struct = {client_tool, skill, memory, subagent}`` — declared, not derived.

    It is the set ``ν`` counts over and the set exploration reserves first, so
    two call sites must never be able to compute two different answers.
    """
    assert STRUCTURAL_COMPONENTS == frozenset({"client_tool", "skill", "memory", "subagent"})
    assert STRUCTURAL_COMPONENTS < frozenset(COMPONENTS)


def test_surface_map_covers_exactly_the_evolvable_surfaces():
    """The key set is pinned to the generator's allow-set, not to a copy of it.

    A surface the generator accepts but this table does not map would raise at
    attribution time; a surface mapped here but not evolvable would be dead
    provenance. Both directions are checked against the real constant.
    """
    assert set(SURFACE_COMPONENTS) == set(EVOLVABLE_SURFACES)
    for surface, component in SURFACE_COMPONENTS.items():
        assert component in COMPONENTS, f"surface {surface!r} maps to {component!r}, which is not in K"


def test_a_target_hint_beats_the_surface_default():
    tag = component_for("code", target="compaction")
    assert (tag.component, tag.basis, tag.matched_hint) == ("context_mgmt", "target_hint", "compaction")
    assert tag.surface == "code"


def test_a_surface_default_is_reported_as_a_default_not_a_hint():
    assert component_for("code").basis == "surface_default"
    # A target with no matching hint falls back to the surface's component and
    # says so — the attribution is still a heuristic, not a measurement.
    assert component_for("code", target="something_unmapped").basis == "surface"


def test_an_unknown_surface_is_refused_rather_than_defaulted():
    """A silent fallback would file every typo under one component."""
    with pytest.raises(ValueError, match="no RRSI component mapping"):
        component_for("secrets")
    with pytest.raises(ValueError, match="unknown RRSI component"):
        validate_component("prompt_engine")


def test_several_targets_deduplicate_by_component():
    tags = components_for("code", targets=["tool_router", "routing"])
    assert [tag.component for tag in tags] == ["control_flow"]


def test_novelty_counts_only_untried_structural_members():
    assert novelty(("client_tool", "prompt"), frozenset()) == 1  # prompt is not structural
    assert novelty(("client_tool", "skill"), frozenset()) == 2
    assert novelty(("client_tool", "skill"), frozenset({"client_tool"})) == 1
    assert novelty(("prompt",), frozenset()) == 0


def test_novelty_is_unknown_not_zero_when_the_ledger_could_not_be_read():
    assert novelty(("client_tool",), None) is None
    assert novelty(None, frozenset()) is None
    breakdown = novelty_breakdown(("client_tool",), None)
    assert breakdown["novelty"] is None
    assert "ledger unavailable" in str(breakdown["reason"])


def test_novelty_breakdown_names_both_memberships_it_used():
    breakdown = novelty_breakdown(("client_tool", "skill"), frozenset())
    assert breakdown["novelty"] == 2
    assert breakdown["structural_touched"] == ["client_tool", "skill"]
    assert breakdown["never_won"] == ["client_tool", "skill"]
    assert breakdown["reason"] == ""
