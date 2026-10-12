"""Tests for tool-and-model joint routing (Feature M / plan §14).

A model config is never dispatched alone: it is selected as ONE unit with
instructions, skills, toolset, context slice and a verification method, and
each unit resolves to exactly ONE availability state that names the failing
component. The load-bearing rules pinned here: the plan's motivating example
(exact arithmetic → inexpensive model + deterministic calculator) routes
without the router ever knowing a price; a missing credential or endpoint
record is unknown and fails closed; "installed" and "permitted" stay distinct
tool failures; a joint unit with no executable verification is not runnable;
bounds are refused, never clamped; and nothing here executes anything.
"""

from __future__ import annotations

import inspect

import pytest

from alpha.capability_mesh.joint_routing import (
    Availability,
    ExecutionConfiguration,
    JointComponent,
    JointEligibility,
    JointRouter,
)

CHEAP = "cheap/arith"
EXPENSIVE = "expensive/chat"


def _configuration(**kwargs: object) -> ExecutionConfiguration:
    base: dict[str, object] = {
        "model_config": CHEAP,
        "instructions_ref": "instructions/arith@v1",
        "skills": ("exact_arithmetic",),
        "toolset": ("calculator",),
        "context_slice_ids": ("slice/problem",),
        "verification_method": "unit-tests",
    }
    base.update(kwargs)
    return ExecutionConfiguration(**base)  # type: ignore[arg-type]


def _router(**kwargs: object) -> JointRouter:
    base: dict[str, object] = {
        "capabilities": {CHEAP: ["exact_arithmetic"], EXPENSIVE: ["prose"]},
        "credentials": {CHEAP: True, EXPENSIVE: True},
        "allowed_tools": frozenset({"calculator", "test_runner"}),
        "verification_executable": frozenset({"unit-tests"}),
        "endpoint_up": {CHEAP: True, EXPENSIVE: True},
        "runtime_ok": {CHEAP: True, EXPENSIVE: True},
    }
    base.update(kwargs)
    return JointRouter(**base)  # type: ignore[arg-type]


def _only(router_kwargs: dict[str, object], **configuration_kwargs: object) -> JointEligibility:
    """Evaluate a single joint unit and hand back its one row."""
    rows = _router(configurations=(_configuration(**configuration_kwargs),), **router_kwargs).evaluate(["exact_arithmetic"], ["calculator"])
    assert len(rows) == 1
    return rows[0]


class TestJointEligibilityStates:
    @pytest.mark.parametrize(
        ("availability", "failed_component", "router_kwargs", "configuration_kwargs"),
        [
            (Availability.AVAILABLE, None, {}, {}),
            (Availability.MODEL_LACKS_CAPABILITY, JointComponent.MODEL, {"capabilities": {CHEAP: ["prose"]}}, {}),
            (Availability.ENDPOINT_DOWN, JointComponent.MODEL, {"endpoint_up": {CHEAP: False}}, {}),
            (Availability.CREDENTIALS_INVALID, JointComponent.MODEL, {"credentials": {CHEAP: False}}, {}),
            (Availability.TOOL_MISSING, JointComponent.TOOLSET, {}, {"toolset": ("nonexistent_calculator",)}),
            (Availability.PERMISSION_DENIED, JointComponent.TOOLSET, {"allowed_tools": frozenset({"test_runner"})}, {}),
            (Availability.RUNTIME_LIMIT, JointComponent.MODEL, {"runtime_ok": {CHEAP: False}}, {}),
            (Availability.VERIFICATION_UNRUNNABLE, JointComponent.VERIFICATION_METHOD, {"verification_executable": frozenset()}, {}),
        ],
    )
    def test_one_row_per_availability_state(self, availability: Availability, failed_component: JointComponent | None, router_kwargs: dict[str, object], configuration_kwargs: dict[str, object]) -> None:
        row = _only(router_kwargs, **configuration_kwargs)
        assert row.availability is availability
        assert row.failed_component is failed_component
        assert row.reason.strip(), "every availability state carries an observable reason"

    def test_every_planned_state_is_reachable_and_distinct(self) -> None:
        assert {state.value for state in Availability} == {
            "model_lacks_capability",
            "available",
            "endpoint_down",
            "tool_missing",
            "permission_denied",
            "credentials_invalid",
            "runtime_limit",
            "verification_unrunnable",
        }

    def test_the_reason_names_the_failing_component(self) -> None:
        assert JointComponent.MODEL.value in _only({"endpoint_up": {CHEAP: False}}).reason
        assert JointComponent.TOOLSET.value in _only({"allowed_tools": frozenset({"test_runner"})}).reason
        assert JointComponent.VERIFICATION_METHOD.value in _only({"verification_executable": frozenset()}).reason

    def test_an_available_unit_says_so_without_claiming_more_than_it_checked(self) -> None:
        row = _only({})
        assert row.reason.startswith("available:")
        assert "declared, not verified here" in row.reason

    def test_unknown_records_fail_closed(self) -> None:
        # No record at all is unknown — never "assumed fine".
        row = _only({"endpoint_up": {}, "credentials": {}, "runtime_ok": {}})
        assert row.availability is Availability.ENDPOINT_DOWN
        assert "unknown" in row.reason.lower()


class TestMotivatingExample:
    def test_exact_arithmetic_routes_to_the_calculator_joint_unit(self) -> None:
        """Plan §14.2: an inexpensive model plus a deterministic calculator,
        rather than several chat agents."""
        rows = _router(configurations=(_configuration(), _configuration(model_config=EXPENSIVE, instructions_ref="instructions/prose@v1", toolset=("calculator",)))).evaluate(["exact_arithmetic"], ["calculator"])
        by_model = {row.configuration.model_config: row for row in rows}
        assert by_model[CHEAP].availability is Availability.AVAILABLE
        assert by_model[EXPENSIVE].availability is Availability.MODEL_LACKS_CAPABILITY
        assert by_model[EXPENSIVE].failed_component is JointComponent.MODEL

    def test_the_router_surface_carries_no_pricing_input(self) -> None:
        init_params = set(inspect.signature(JointRouter.__init__).parameters)
        assert init_params == {"self", "capabilities", "credentials", "allowed_tools", "verification_executable", "endpoint_up", "runtime_ok", "configurations"}
        assert set(inspect.signature(JointRouter.evaluate).parameters) == {"self", "required_capabilities", "required_tools"}

    def test_evaluation_is_deterministic_and_ordered(self) -> None:
        configurations = (_configuration(), _configuration(model_config=EXPENSIVE, instructions_ref="instructions/prose@v1", skills=("prose",), toolset=()))
        router = _router(configurations=configurations)
        first = router.evaluate(["exact_arithmetic"], ["calculator"])
        second = router.evaluate(["exact_arithmetic"], ["calculator"])
        assert first == second
        assert [row.configuration.model_config for row in first] == [CHEAP, EXPENSIVE]


class TestToolDistinction:
    def test_a_tool_missing_from_the_configuration_is_not_the_same_as_a_denied_one(self) -> None:
        missing = _only({}, toolset=())
        denied = _only({"allowed_tools": frozenset()}, toolset=("calculator",))
        assert missing.availability is Availability.TOOL_MISSING
        assert denied.availability is Availability.PERMISSION_DENIED
        assert missing.reason != denied.reason

    def test_only_required_tools_are_gated(self) -> None:
        # The configuration carries a wider toolset than the task needs; an
        # unused, unpermitted tool must not fail the unit.
        row = _only({"allowed_tools": frozenset({"calculator"})}, toolset=("calculator", "dangerous_admin_tool"))
        assert row.availability is Availability.AVAILABLE

    def test_a_task_needing_no_tool_still_runs(self) -> None:
        rows = _router(configurations=(_configuration(),)).evaluate(["exact_arithmetic"], [])
        assert rows[0].availability is Availability.AVAILABLE


class TestRuntimeDisclosure:
    def test_no_runtime_registry_discloses_that_runtime_was_not_checked(self) -> None:
        row = _only({"runtime_ok": None})
        assert row.availability is Availability.AVAILABLE
        assert "runtime" in row.reason.lower()
        assert "not checked" in row.reason.lower()

    def test_a_runtime_registry_without_a_row_fails_closed(self) -> None:
        row = _only({"runtime_ok": {EXPENSIVE: True}})
        assert row.availability is Availability.RUNTIME_LIMIT


class TestRefusals:
    def test_empty_model_config_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            _configuration(model_config="")
        assert "model_config" in str(error.value)

    def test_empty_instructions_ref_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            _configuration(instructions_ref="")
        assert "instructions_ref" in str(error.value)

    def test_empty_verification_method_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            _configuration(verification_method="")
        assert "verification_method" in str(error.value)

    def test_a_verification_method_is_required_to_exist_on_the_unit(self) -> None:
        with pytest.raises(ValueError) as error:
            ExecutionConfiguration(model_config=CHEAP, instructions_ref="instructions/arith@v1")  # type: ignore[call-arg]
        assert "verification_method" in str(error.value)

    def test_an_empty_slice_entry_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            _configuration(context_slice_ids=("",))
        assert "context_slice_ids" in str(error.value)

    def test_an_available_unit_may_not_name_a_failed_component(self) -> None:
        with pytest.raises(ValueError) as error:
            JointEligibility(configuration=_configuration(), availability=Availability.AVAILABLE, failed_component=JointComponent.MODEL, reason="because")
        assert "failed_component" in str(error.value)

    def test_an_unavailable_unit_must_name_its_failed_component(self) -> None:
        with pytest.raises(ValueError) as error:
            JointEligibility(configuration=_configuration(), availability=Availability.ENDPOINT_DOWN, failed_component=None, reason="the endpoint is down")
        assert "failed_component" in str(error.value)

    def test_an_empty_reason_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            JointEligibility(configuration=_configuration(), availability=Availability.ENDPOINT_DOWN, failed_component=JointComponent.MODEL, reason="  ")
        assert "reason" in str(error.value)

    def test_a_router_with_no_models_routes_nothing(self) -> None:
        with pytest.raises(ValueError) as error:
            _router(capabilities={})
        assert "capabilities" in str(error.value)

    def test_a_non_boolean_credential_state_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            _router(credentials={CHEAP: "yes"})  # type: ignore[dict-item]
        assert "credentials" in str(error.value)

    def test_evaluating_without_a_required_capability_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            _router(configurations=(_configuration(),)).evaluate([], ["calculator"])
        assert "required_capabilities" in str(error.value)

    def test_an_empty_candidate_list_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            _router(configurations=())
        assert "configurations" in str(error.value)

    def test_a_non_configuration_candidate_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            _router(configurations=("cheap/arith",))  # type: ignore[arg-type]
        assert "cheap/arith" in str(error.value)


class TestDefaultsAndRoundTrips:
    def test_without_configurations_the_router_synthesises_one_unit_per_model(self) -> None:
        rows = _router(runtime_ok=None).evaluate(["exact_arithmetic"], ["calculator"])
        assert [row.configuration.model_config for row in rows] == [CHEAP, EXPENSIVE]
        # The synthesised unit declares no verification method, and a unit with
        # no runnable verification is not an available one.
        assert rows[0].availability is Availability.VERIFICATION_UNRUNNABLE
        assert rows[0].configuration.instructions_ref == "unspecified"

    def test_configuration_round_trips(self) -> None:
        configuration = _configuration()
        assert ExecutionConfiguration.from_dict(configuration.to_dict()) == configuration

    def test_eligibility_round_trips(self) -> None:
        row = _only({"allowed_tools": frozenset({"test_runner"})})
        assert JointEligibility.from_dict(row.to_dict()) == row

    def test_a_single_enumeration_is_the_joint_component_vocabulary(self) -> None:
        assert [component.value for component in JointComponent] == ["model", "instructions", "skills", "toolset", "context_slice", "verification_method"]
