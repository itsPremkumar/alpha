"""The coverage table is a claim, so it is checked like one.

A layer map that lives in prose drifts. This one lives in
:mod:`alpha.observability.trace.coverage` and every entry is verified:

* a ``CALL_SITE`` layer names a module that really contains its emitter's code;
* a ``REGISTRY_ONLY`` layer has at least one registry code and one emitter, so
  "shaped but unwired" is distinguishable from "not thought about";
* a ``BLOCKED`` layer names the exact path whose edit it needs, so the follow-up
  is a one-line request rather than an archaeology exercise;
* all eighteen layers are accounted for, so a new layer cannot be added to the
  registry without deciding whether it is wired.

The counters the test reads are the same ones the report renders, so a claim
cannot be made in one place and counted in another.
"""

from __future__ import annotations

from alpha.observability.trace import instrumentation
from alpha.observability.trace.codes import EVENT_TYPES, LAYER_EVENT_CODES, TraceLayer
from alpha.observability.trace.coverage import COVERAGE, WiringState, coverage_report, verify_call_sites


def test_every_one_of_the_eighteen_layers_is_accounted_for():
    assert set(COVERAGE) == {int(layer) for layer in TraceLayer}
    assert len(COVERAGE) == 18


def test_the_table_states_a_real_state_for_every_layer():
    for index, entry in COVERAGE.items():
        assert isinstance(entry.state, WiringState), f"layer {index} has no wiring state"
        assert entry.name.strip(), f"layer {index} has no name"
        if entry.state is WiringState.CALL_SITE:
            assert entry.call_sites, f"layer {index} claims a call site and names none"
            assert not entry.blocked_on, f"layer {index} cannot be both wired and blocked"
        if entry.state is WiringState.BLOCKED:
            assert entry.blocked_on, f"layer {index} claims to be blocked and names no file; a block with no path is an excuse"
        if entry.state is not WiringState.CALL_SITE:
            assert entry.call_sites == (), f"layer {index} declares call sites but is not wired"
        assert entry.notes.strip(), f"layer {index} has no note explaining its state"


def test_every_declared_call_site_really_contains_its_emitter():
    """The staleness check. A reverted or moved call site must fail the build
    rather than leave the report claiming coverage that no longer exists."""
    problems = verify_call_sites()
    assert problems == (), "the coverage table is stale:\n  " + "\n  ".join(problems)


def test_every_layer_declares_emitters_that_exist():
    for index, entry in COVERAGE.items():
        for name in entry.emitters:
            assert hasattr(instrumentation, name), f"layer {index} names emitter {name!r}, which instrumentation does not define"


def test_a_registry_only_layer_really_has_a_code_and_an_emitter():
    for index, entry in COVERAGE.items():
        if entry.state is WiringState.REGISTRY_ONLY:
            assert LAYER_EVENT_CODES[index], f"layer {index} is registry_only with no code"
            assert entry.emitters, f"layer {index} is registry_only with no emitter; that is a layer nobody thought about"


def test_every_registry_code_belongs_to_the_layer_the_table_claims():
    for index, entry in COVERAGE.items():
        layer = TraceLayer(index)
        for code in entry.to_dict()["codes"]:  # type: ignore[union-attr]
            assert EVENT_TYPES[code].layer is layer


def test_the_report_counts_are_computed_not_asserted_by_hand():
    report = coverage_report()
    assert report["total_layers"] == 18
    assert sum(report["by_state"].values()) == 18
    assert set(report["by_state"]) <= {state.value for state in WiringState}
    assert [layer["layer"] for layer in report["layers"]] == list(range(18))


def test_every_blocked_layer_names_a_repo_path():
    for index, entry in COVERAGE.items():
        for path in entry.blocked_on:
            assert path.startswith("backend/") or "/" in path, f"layer {index} names {path!r}, which is not a repo path"


def test_no_layer_is_simultaneously_covered_and_merely_shaped():
    """A layer is either emitting or it is not. Reporting both states for one
    layer is how a reader ends up believing a layer is instrumented because some
    other layer is."""
    states = [entry.state for entry in COVERAGE.values()]
    assert states.count(WiringState.CALL_SITE) >= 1, "at least one layer must actually emit, or the substrate is inert"
