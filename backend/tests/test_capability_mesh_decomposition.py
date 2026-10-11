"""Tests for microtask decomposition (plan §3.2): the contract between fingerprint and routing.

Every microtask carries explicit acceptance criteria (plan invariant), the
dependency graph is validated acyclic/resolvable/unique, the fan-out is
bounded, and the model seam parses defensively — with a failed or absent
generation disclosed, never silently swapped for the heuristic.
"""

from __future__ import annotations

import json

import pytest

from alpha.capability_mesh.decomposition import (
    HEURISTIC_NOTES,
    MAX_MICROTASKS,
    MAX_MODEL_OUTPUT_CHARS,
    MAX_SUMMARY_CHARS,
    MAX_TITLE_CHARS,
    Decomposition,
    DecompositionError,
    Microtask,
    decompose_heuristic,
    decompose_model,
    try_decompose,
)
from alpha.capability_mesh.fingerprint import Band, Pressure, Risk, SituationFingerprint


def _fp(**kwargs: object) -> SituationFingerprint:
    base: dict[str, object] = {"task_type": "general", "risk_band": Risk.LOW, "context_pressure": Pressure.LOW}
    base.update(kwargs)
    return SituationFingerprint(**base)  # type: ignore[arg-type]


def _mt(microtask_id: str = "mt-1", **kwargs: object) -> Microtask:
    base: dict[str, object] = {"title": "a title", "summary": "a summary", "acceptance_checks": ("the check passes",)}
    base.update(kwargs)
    return Microtask(microtask_id=microtask_id, **base)  # type: ignore[arg-type]


class TestMicrotask:
    def test_id_title_summary_required_non_empty(self) -> None:
        with pytest.raises(ValueError, match="microtask_id"):
            _mt("")
        with pytest.raises(ValueError, match="title"):
            _mt(title="")
        with pytest.raises(ValueError, match="summary"):
            _mt(summary="")

    def test_summary_and_title_are_bounded(self) -> None:
        with pytest.raises(ValueError, match=f"at most {MAX_SUMMARY_CHARS}"):
            _mt(summary="x" * (MAX_SUMMARY_CHARS + 1))
        with pytest.raises(ValueError, match=f"at most {MAX_TITLE_CHARS}"):
            _mt(title="x" * (MAX_TITLE_CHARS + 1))
        # The bound is inclusive: exactly at the limit is fine.
        _mt(summary="x" * MAX_SUMMARY_CHARS)

    def test_acceptance_checks_required_non_empty(self) -> None:
        with pytest.raises(ValueError, match="acceptance_checks must be non-empty"):
            _mt(acceptance_checks=())
        with pytest.raises(ValueError, match="acceptance_checks entries"):
            _mt(acceptance_checks=("",))

    def test_max_attempts_refused_below_one_and_for_bool(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            _mt(max_attempts=0)
        with pytest.raises(ValueError, match="max_attempts"):
            _mt(max_attempts=-1)
        with pytest.raises(ValueError, match="max_attempts"):
            _mt(max_attempts=True)

    def test_dependencies_are_validated_by_the_set_not_here(self) -> None:
        # One microtask cannot know its siblings; an unknown dependency is
        # perfectly fine at this level and refused by Decomposition.
        _mt(depends_on=("mt-9",))

    def test_round_trip(self) -> None:
        microtask = _mt(required_capabilities=("code",), required_tools=("bash",), depends_on=(), max_attempts=3)
        assert Microtask.from_dict(microtask.to_dict()) == microtask


class TestDecompositionValidation:
    def _chain(self) -> tuple[Microtask, Microtask]:
        return (_mt("mt-1"), _mt("mt-2", depends_on=("mt-1",)))

    def test_empty_microtask_list_is_refused(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            Decomposition(fingerprint=_fp(), microtasks=(), decomposer="model")

    def test_fan_out_is_bounded(self) -> None:
        too_many = tuple(_mt(f"mt-{index}") for index in range(MAX_MICROTASKS + 1))
        with pytest.raises(ValueError, match=f"at most {MAX_MICROTASKS}"):
            Decomposition(fingerprint=_fp(), microtasks=too_many, decomposer="model")
        exactly = tuple(_mt(f"mt-{index}") for index in range(MAX_MICROTASKS))
        Decomposition(fingerprint=_fp(), microtasks=exactly, decomposer="model")  # boundary is inclusive

    def test_duplicate_ids_refused_by_name(self) -> None:
        with pytest.raises(ValueError, match="duplicate microtask_id"):
            Decomposition(fingerprint=_fp(), microtasks=(_mt("mt-1"), _mt("mt-1")), decomposer="heuristic_baseline")

    def test_unresolved_dependency_refused_naming_it(self) -> None:
        with pytest.raises(ValueError, match="unknown microtask 'mt-9'"):
            Decomposition(fingerprint=_fp(), microtasks=(_mt("mt-1", depends_on=("mt-9",)),), decomposer="heuristic_baseline")

    def test_self_dependency_refused(self) -> None:
        with pytest.raises(ValueError, match="depends on itself"):
            Decomposition(fingerprint=_fp(), microtasks=(_mt("mt-1", depends_on=("mt-1",)),), decomposer="heuristic_baseline")

    def test_cycle_refused_by_name(self) -> None:
        cyclic = (_mt("mt-1", depends_on=("mt-2",)), _mt("mt-2", depends_on=("mt-1",)))
        with pytest.raises(ValueError) as caught:
            Decomposition(fingerprint=_fp(), microtasks=cyclic, decomposer="model")
        message = str(caught.value)
        assert "cycle" in message and "mt-1" in message and "mt-2" in message

    def test_valid_dependency_chain_accepted(self) -> None:
        first, second = self._chain()
        plan = Decomposition(fingerprint=_fp(), microtasks=(first, second), decomposer="model")
        assert plan.microtasks[1].depends_on == ("mt-1",)

    def test_decomposer_and_notes_honesty(self) -> None:
        with pytest.raises(ValueError, match="decomposer"):
            Decomposition(fingerprint=_fp(), microtasks=(_mt(),), decomposer="")
        with pytest.raises(ValueError, match="notes entries"):
            Decomposition(fingerprint=_fp(), microtasks=(_mt(),), decomposer="model", notes=("",))

    def test_round_trip(self) -> None:
        first, second = self._chain()
        plan = Decomposition(fingerprint=_fp(complexity_band=Band.HIGH), microtasks=(first, second), decomposer="model", notes=("a note",))
        assert Decomposition.from_dict(plan.to_dict()) == plan


class TestHeuristicBaseline:
    def test_one_microtask_per_declared_capability(self) -> None:
        fingerprint = _fp(required_capabilities=("code", "review", "explain"))
        plan = decompose_heuristic(fingerprint, "fix the bug")
        assert plan.decomposer == "heuristic_baseline"
        assert [task.microtask_id for task in plan.microtasks] == ["mt-1", "mt-2", "mt-3"]
        assert [task.required_capabilities for task in plan.microtasks] == [("code",), ("review",), ("explain",)]
        # Each acceptance check references its capability (plan §3.2: objective criteria per step).
        for task, capability in zip(plan.microtasks, ("code", "review", "explain")):
            assert capability in task.acceptance_checks[0]
            assert task.acceptance_checks  # non-empty: no assignment without criteria

    def test_declared_tools_travel_to_every_slice(self) -> None:
        fingerprint = _fp(required_capabilities=("code", "review"), required_tools=("bash",))
        plan = decompose_heuristic(fingerprint, "fix the bug")
        assert all(task.required_tools == ("bash",) for task in plan.microtasks)

    def test_no_capabilities_stays_single_step(self) -> None:
        plan = decompose_heuristic(_fp(), "a tiny request")
        assert len(plan.microtasks) == 1
        assert plan.microtasks[0].microtask_id == "mt-1"
        assert plan.microtasks[0].acceptance_checks  # even the whole task needs criteria

    def test_empty_task_text_refused(self) -> None:
        with pytest.raises(ValueError, match="non-empty task_text"):
            decompose_heuristic(_fp(), "")

    def test_notes_disclose_the_crude_baseline(self) -> None:
        plan = decompose_heuristic(_fp(), "whatever")
        assert HEURISTIC_NOTES[0] in plan.notes

    def test_deterministic(self) -> None:
        fingerprint = _fp(required_capabilities=("code", "review"))
        assert decompose_heuristic(fingerprint, "text") == decompose_heuristic(fingerprint, "text")


class TestModelSeam:
    def _generate(self, document: object) -> object:
        return lambda _prompt: document if isinstance(document, str) else json.dumps(document)

    def _valid(self) -> dict[str, object]:
        return {
            "microtasks": [
                {
                    "microtask_id": "mt-1",
                    "title": "reproduce",
                    "summary": "run the failing case",
                    "required_capabilities": ["debugging"],
                    "required_tools": [],
                    "acceptance_checks": ["failure reproduced"],
                    "depends_on": [],
                    "max_attempts": 2,
                }
            ]
        }

    def test_valid_generation_parses_and_names_the_model(self) -> None:
        plan = decompose_model(_fp(), "task", generate=self._generate(self._valid()))
        assert plan.decomposer == "model"
        assert plan.notes == ()
        assert plan.microtasks[0].max_attempts == 2
        assert plan.microtasks[0].required_capabilities == ("debugging",)

    def test_absent_generation_is_refused_not_fallen_back(self) -> None:
        with pytest.raises(DecompositionError, match="no output"):
            decompose_model(_fp(), "task", generate=lambda _prompt: None)

    def test_non_string_generation_refused(self) -> None:
        with pytest.raises(DecompositionError, match="expected a JSON string"):
            decompose_model(_fp(), "task", generate=lambda _prompt: 42)  # type: ignore[arg-type, return-value]

    def test_generation_exception_propagates_as_disclosed_failure(self) -> None:
        def boom(_prompt: str) -> str:
            raise RuntimeError("gateway timeout")

        with pytest.raises(DecompositionError, match="gateway timeout"):
            decompose_model(_fp(), "task", generate=boom)

    def test_oversized_output_refused_before_parsing(self) -> None:
        padded = " " * (MAX_MODEL_OUTPUT_CHARS + 1)
        with pytest.raises(DecompositionError, match=f"over the {MAX_MODEL_OUTPUT_CHARS}-char bound"):
            decompose_model(_fp(), "task", generate=lambda _prompt: padded)

    def test_non_json_refused_naming_the_cause(self) -> None:
        with pytest.raises(DecompositionError, match="not valid JSON"):
            decompose_model(_fp(), "task", generate=lambda _prompt: "Sure! Here are the microtasks: ...")

    def test_fenced_json_is_still_non_json(self) -> None:
        fenced = f"```json\n{json.dumps(self._valid())}\n```"
        with pytest.raises(DecompositionError, match="not valid JSON"):
            decompose_model(_fp(), "task", generate=lambda _prompt: fenced)

    def test_top_level_shape_is_strict(self) -> None:
        with pytest.raises(DecompositionError, match="must be a JSON object"):
            decompose_model(_fp(), "task", generate=lambda _prompt: "[]")
        with pytest.raises(DecompositionError, match="missing the required 'microtasks' key"):
            decompose_model(_fp(), "task", generate=lambda _prompt: "{}")
        with pytest.raises(DecompositionError, match="unknown key"):
            decompose_model(_fp(), "task", generate=lambda _prompt: json.dumps({"microtasks": [], "notes": []}))
        with pytest.raises(DecompositionError, match="must be an array"):
            decompose_model(_fp(), "task", generate=lambda _prompt: json.dumps({"microtasks": {}}))

    def test_unknown_microtask_keys_refused_naming_them(self) -> None:
        document = self._valid()
        rows = document["microtasks"]  # type: ignore[index]
        rows[0]["creativity_score"] = 9000  # type: ignore[index]
        with pytest.raises(DecompositionError, match="unknown key"):
            decompose_model(_fp(), "task", generate=self._generate(document))

    def test_empty_list_refused(self) -> None:
        with pytest.raises(DecompositionError, match="contains no microtasks"):
            decompose_model(_fp(), "task", generate=lambda _prompt: json.dumps({"microtasks": []}))

    def test_over_max_refused(self) -> None:
        rows = [
            {
                "microtask_id": f"mt-{index}",
                "title": "t",
                "summary": "s",
                "acceptance_checks": ["c"],
            }
            for index in range(MAX_MICROTASKS + 1)
        ]
        with pytest.raises(DecompositionError, match=f"over the MAX_MICROTASKS bound of {MAX_MICROTASKS}"):
            decompose_model(_fp(), "task", generate=lambda _prompt: json.dumps({"microtasks": rows}))

    def test_missing_required_field_refused(self) -> None:
        document = self._valid()
        del document["microtasks"][0]["acceptance_checks"]  # type: ignore[index]
        with pytest.raises(DecompositionError, match="missing the required key"):
            decompose_model(_fp(), "task", generate=self._generate(document))

    def test_empty_acceptance_checks_refused(self) -> None:
        document = self._valid()
        document["microtasks"][0]["acceptance_checks"] = []  # type: ignore[index]
        with pytest.raises(DecompositionError, match="acceptance_checks"):
            decompose_model(_fp(), "task", generate=self._generate(document))

    def test_cycle_in_generation_refused_by_the_same_validation(self) -> None:
        document = self._valid()
        document["microtasks"].append(  # type: ignore[union-attr]
            {
                "microtask_id": "mt-2",
                "title": "t",
                "summary": "s",
                "acceptance_checks": ["c"],
                "depends_on": ["mt-1"],
            }
        )
        document["microtasks"][0]["depends_on"] = ["mt-2"]  # type: ignore[index]
        with pytest.raises(DecompositionError, match="cycle"):
            decompose_model(_fp(), "task", generate=self._generate(document))


class TestTryDecompose:
    def test_no_generator_falls_back_with_disclosure(self) -> None:
        plan = try_decompose(_fp(required_capabilities=("code",)), "task")
        assert plan.decomposer == "heuristic_baseline"
        assert "model decomposition unavailable; heuristic baseline used" in plan.notes

    def test_failed_generation_falls_back_and_carries_the_cause(self) -> None:
        plan = try_decompose(_fp(), "task", generate=lambda _prompt: "not json")
        assert plan.decomposer == "heuristic_baseline"
        assert "model decomposition unavailable; heuristic baseline used" in plan.notes
        assert any("not valid JSON" in note for note in plan.notes)

    def test_successful_generation_is_not_swapped(self) -> None:
        document = {
            "microtasks": [
                {"microtask_id": "mt-1", "title": "t", "summary": "s", "acceptance_checks": ["c"]},
            ]
        }
        plan = try_decompose(_fp(), "task", generate=lambda _prompt: json.dumps(document))
        assert plan.decomposer == "model"
        assert "model decomposition unavailable; heuristic baseline used" not in plan.notes
