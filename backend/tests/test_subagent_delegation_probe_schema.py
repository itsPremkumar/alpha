"""Offline tests for the W1 delegation telemetry probe.

Two layers are pinned here, and both are pure (no Gateway, no model calls):

1. **The report schema** (``scripts/support/subagent_probe_report.py``) — the
   contract between the probe and its consumers. The tests pin the full-key
   serialization (``None`` -> JSON ``null``, never a stand-in ``0``/``""``/``[]``),
   the ``0`` vs ``None`` distinction that a reader takes opposite decisions from,
   the outcome/expectation consistency rule, the exit-code mapping (0/1/2) and
   the delete-a-captured-field negative control that makes schema drift a
   named violation instead of a silently different file shape.
2. **The probe's pure evaluators** (``scripts/subagent_delegation_probe.py``) —
   ``_eval_*`` / ``_apply_outcome`` decide ``met``/``unmet``/``incomplete`` from
   measurements alone, so they can be exercised with synthetic reports. A
   finding (``unmet``/``refused``) is data; a missing measurement is
   ``incomplete`` (exit 1); a shape breach is exit 2.
3. **The report's crash path** (``run_probe`` itself, with the network seams
   monkeypatched): a Gateway that dies mid-probe — including one that dies
   before the trailing version read — must still produce a written
   ``incomplete`` report, never a traceback with nothing on disk (regression
   for the 2026-10-17 full-catalogue run, which died exactly there).

Plan acceptance gates: D3 (schema + null honesty pinned offline) and the
offline half of D4 (a Gateway death reported as ``incomplete`` with a reason and
exit code 1). D1 (live evidence) is produced by running the probe itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import pytest

import scripts.subagent_delegation_probe as probe_module
from scripts.subagent_delegation_probe import (
    _apply_outcome,
    _eval_acceptance_leaf,
    _eval_batch,
    _eval_control,
    _eval_direct_answer,
    _eval_parallel_fanout,
    _eval_quick_category,
    _eval_single_delegation,
    _eval_turn_capped,
    _select_scenarios,
    scenario_catalogue,
)
from scripts.support.subagent_probe_report import (
    REPORT_FIELDS,
    REPORT_SCHEMA_VERSION,
    SCENARIO_FIELDS,
    UNINSTRUMENTED_FIELDS,
    ProbeReport,
    ScenarioReport,
    render_markdown,
    report_exit_code,
    to_jsonable,
    validate_report,
)

UNMEASURED_SCENARIO_FIELDS = (
    "stop_reasons",
    "delegations",
    "models",
    "tokens_by_model",
    "tokens_by_child",
    "wall_seconds",
    "queue_wait_seconds",
    "acceptance_verdicts",
    "receipts",
    "artifacts",
    "event_persistence",
)


def _scenario(**overrides: object) -> ScenarioReport:
    base: dict[str, object] = {
        "key": "single_delegation",
        "title": "test scenario",
        "expectation": "the measurement lands",
        "outcome": "met",
        "detail": "measured",
        "stream_modes": ["messages-tuple", "values", "custom"],
        "expectation_satisfied": True,
    }
    base.update(overrides)
    return ScenarioReport(**base)  # type: ignore[arg-type]


def _report(scenarios: list[ScenarioReport] | None = None, *, verdict: str = "complete", incomplete_reason: str | None = None) -> ProbeReport:
    return ProbeReport(
        run_id="20260101T000000Z-deadbeef",
        started_at="2026-01-01T00:00:00+00:00",
        base_url="http://127.0.0.1:8001",
        platform="test-platform",
        stream_modes=["messages-tuple", "values", "custom"],
        ended_at="2026-01-01T00:01:00+00:00",
        verdict=verdict,
        incomplete_reason=incomplete_reason,
        scenarios=scenarios if scenarios is not None else [_scenario()],
    )


class TestReportSchema:
    def test_serialized_report_keeps_every_schema_key(self) -> None:
        payload = to_jsonable(_report())
        assert set(payload) == set(REPORT_FIELDS)
        assert set(payload["scenarios"][0]) == set(SCENARIO_FIELDS)
        assert payload["schema_version"] == REPORT_SCHEMA_VERSION

    def test_unmeasured_fields_serialize_as_null_never_as_zero_or_empty(self) -> None:
        payload = to_jsonable(_report())
        scenario = payload["scenarios"][0]
        for name in UNMEASURED_SCENARIO_FIELDS:
            assert scenario[name] is None, f"{name} must stay null until measured"
        assert validate_report(payload) == []

    def test_measured_zero_is_distinct_from_unmeasured(self) -> None:
        measured = _scenario(delegations=0, stop_reasons=[], models=None)
        payload = to_jsonable(_report([measured]))
        scenario = payload["scenarios"][0]
        assert scenario["delegations"] == 0  # a measurement: no delegations happened
        assert scenario["stop_reasons"] == []  # a measurement: no cap fired
        assert validate_report(payload) == []

        unmeasured = _scenario(delegations=None, stop_reasons=None)
        payload = to_jsonable(_report([unmeasured]))
        scenario = payload["scenarios"][0]
        assert scenario["delegations"] is None  # not measured, not zero
        assert scenario["stop_reasons"] is None
        assert validate_report(payload) == []

    def test_non_finite_floats_serialize_as_null(self) -> None:
        payload = to_jsonable(_report([_scenario(wall_seconds=float("nan"))]))
        assert payload["scenarios"][0]["wall_seconds"] is None
        assert validate_report(payload) == []

    def test_bool_is_not_an_int_measurement(self) -> None:
        payload = to_jsonable(_report([_scenario(delegations=True)]))
        violations = validate_report(payload)
        assert any("delegations" in v and "expected a non-negative int" in v for v in violations)

    def test_deleting_a_captured_field_fails_validation_naming_the_field(self) -> None:
        payload = to_jsonable(_report([_scenario(delegations=1, wall_seconds=12.5)]))
        assert validate_report(payload) == []
        del payload["scenarios"][0]["delegations"]
        violations = validate_report(payload)
        assert any("delegations" in v and "missing key" in v for v in violations)

    def test_unknown_field_is_reported_as_drift(self) -> None:
        payload = to_jsonable(_report())
        payload["scenarios"][0]["queue_wait_ms"] = 12
        violations = validate_report(payload)
        assert any("queue_wait_ms" in v and "unknown key" in v for v in violations)

    @pytest.mark.parametrize(
        ("outcome", "satisfied", "expect_inconsistent"),
        [
            ("met", True, False),
            ("unmet", False, False),
            ("refused", None, False),
            ("incomplete", None, False),
            ("error", None, False),
            ("met", None, True),
            ("unmet", True, True),
            ("refused", False, True),
            ("error", True, True),
        ],
    )
    def test_outcome_and_expectation_stay_consistent(self, outcome: str, satisfied: bool | None, expect_inconsistent: bool) -> None:
        payload = to_jsonable(_report([_scenario(outcome=outcome, expectation_satisfied=satisfied)]))
        violations = validate_report(payload)
        inconsistent = any("expectation_satisfied" in v for v in violations)
        assert inconsistent is expect_inconsistent

    def test_unknown_outcome_is_a_violation(self) -> None:
        payload = to_jsonable(_report([_scenario(outcome="probably_fine")]))
        violations = validate_report(payload)
        assert any("unknown outcome" in v for v in violations)

    def test_incomplete_report_must_say_why(self) -> None:
        payload = to_jsonable(_report())
        payload["verdict"] = "incomplete"
        violations = validate_report(payload)
        assert any("incomplete_reason" in v for v in violations)

    def test_exit_code_mapping_is_pinned(self) -> None:
        complete = to_jsonable(_report())
        assert report_exit_code(complete) == 0

        incomplete = to_jsonable(_report([_scenario(outcome="incomplete", detail="gateway unreachable: ConnectError", expectation_satisfied=None)], verdict="incomplete", incomplete_reason="gateway unreachable: ConnectError"))
        assert validate_report(incomplete) == []
        assert report_exit_code(incomplete) == 1

        violating = to_jsonable(_report())
        violating["schema_violations"] = ["scenarios[0].delegations: missing key 'delegations'"]
        assert report_exit_code(violating) == 2

    def test_queue_wait_is_declared_uninstrumented(self) -> None:
        assert UNINSTRUMENTED_FIELDS == ("queue_wait_seconds",)
        assert _scenario().queue_wait_seconds is None

    def test_markdown_renders_unmeasured_as_dash_not_zero(self) -> None:
        markdown = render_markdown(to_jsonable(_report()))
        row = next(line for line in markdown.splitlines() if line.startswith("| single_delegation"))
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert cells[4] == "—"  # delegations: unmeasured, not 0
        assert cells[8] == "—"  # queue wait s: uninstrumented, not 0


class TestProbeEvaluators:
    def test_direct_answer_requires_zero_delegations_and_zero_events(self) -> None:
        rep = _scenario(run_status="success", delegations=0, event_persistence={"source": "run_events", "total": 0, "counts": {}, "task_ids": []})
        assert _eval_direct_answer(rep, {})[0] is True

        delegated = _scenario(run_status="success", delegations=1, event_persistence={"source": "run_events", "total": 2, "counts": {"subagent.end": 1}, "task_ids": ["e1"]})
        assert _eval_direct_answer(delegated, {})[0] is False

        unreadable = _scenario(run_status="success", delegations=None, event_persistence=None)
        assert _eval_direct_answer(unreadable, {})[0] is None

    def test_single_delegation_requires_one_delegation_and_telemetry(self) -> None:
        rep = _scenario(
            run_status="success",
            delegations=1,
            models=["alpha-free"],
            tokens_by_child=[{"task_id": "e1", "model": "alpha-free", "input_tokens": 10, "output_tokens": 5, "total_tokens": 15}],
            event_persistence={"source": "run_events", "total": 3, "counts": {"subagent.end": 1}, "task_ids": ["e1"]},
        )
        assert _eval_single_delegation(rep, {})[0] is True

        rep.delegations = 2
        assert _eval_single_delegation(rep, {})[0] is False

        rep.delegations = 1
        rep.tokens_by_child = None  # delegation recorded but no persisted end event
        assert _eval_single_delegation(rep, {})[0] is False

        rep.delegations = None
        assert _eval_single_delegation(rep, {})[0] is None

    def test_single_delegation_accepts_absent_provider_usage(self) -> None:
        # Absent provider usage is not an estimated zero: the model name is the
        # requirement, token counts are recorded only when the provider gave them.
        rep = _scenario(
            run_status="success",
            delegations=1,
            models=["alpha-free"],
            tokens_by_child=[{"task_id": "e1", "model": "alpha-free", "input_tokens": None, "output_tokens": None, "total_tokens": None}],
            event_persistence={"source": "run_events", "total": 1, "counts": {"subagent.end": 1}, "task_ids": ["e1"]},
        )
        assert _eval_single_delegation(rep, {})[0] is True

    def test_parallel_fanout_requires_three_delegations_and_records_overlap(self) -> None:
        rep = _scenario(delegations=3, event_persistence={"source": "run_events", "total": 9, "counts": {"subagent.end": 3}, "task_ids": ["a", "b", "c"]})
        satisfied, detail = _eval_parallel_fanout(rep, {"overlap_pairs": 2})
        assert satisfied is True
        assert "2 task pair(s)" in detail

        rep.delegations = 2
        assert _eval_parallel_fanout(rep, {"overlap_pairs": 2})[0] is False
        rep.delegations = 3
        rep.event_persistence = None
        assert _eval_parallel_fanout(rep, {})[0] is None

    def test_quick_category_requires_a_recorded_model(self) -> None:
        assert _eval_quick_category(_scenario(delegations=1, models=["alpha-free"]), {})[0] is True
        assert _eval_quick_category(_scenario(delegations=1, models=None), {})[0] is False
        assert _eval_quick_category(_scenario(delegations=None), {})[0] is None

    def test_turn_capped_reads_measured_stop_reasons(self) -> None:
        assert _eval_turn_capped(_scenario(stop_reasons=["turn_capped"]), {})[0] is True
        assert _eval_turn_capped(_scenario(stop_reasons=[]), {})[0] is False  # measured: no cap fired
        assert _eval_turn_capped(_scenario(stop_reasons=None), {})[0] is None  # unreadable: unknown

    def test_acceptance_leaf_records_unverified_as_unmet_with_the_reason(self) -> None:
        verified = _scenario(delegations=1, acceptance_verdicts=[{"criterion": "tests_passed:x", "family": "tests_passed", "checked": True, "holds": True, "detail": "matched command output"}])
        assert _eval_acceptance_leaf(verified, family="tests_passed", want_holds=True)[0] is True

        failing = _scenario(delegations=1, acceptance_verdicts=[{"criterion": "tests_passed:x", "family": "tests_passed", "checked": True, "holds": False, "detail": "failing summary"}])
        assert _eval_acceptance_leaf(failing, family="tests_passed", want_holds=False)[0] is True
        assert _eval_acceptance_leaf(failing, family="tests_passed", want_holds=True)[0] is False

        unverified = _scenario(delegations=1, acceptance_verdicts=[{"criterion": "tests_passed:x", "family": "tests_passed", "checked": False, "holds": False, "detail": "ambiguous shell provenance"}])
        satisfied, detail = _eval_acceptance_leaf(unverified, family="tests_passed", want_holds=True)
        assert satisfied is False
        assert "UNVERIFIED" in detail

        missing_verdict = _scenario(delegations=1, acceptance_verdicts=None)
        assert _eval_acceptance_leaf(missing_verdict, family="tests_passed", want_holds=True)[0] is False

        unreadable = _scenario(delegations=None)
        assert _eval_acceptance_leaf(unreadable, family="tests_passed", want_holds=True)[0] is None

    def test_batch_evaluator_accepts_persisted_cancellation(self) -> None:
        rows = [{"id": "b1", "status": "cancelled", "counts": {"cancelled": 2}}]
        assert _eval_batch(_scenario(), {"batches": rows})[0] is True
        assert _eval_batch(_scenario(), {"batches": [{"id": "b1", "status": "running", "counts": {"running": 2}}]})[0] is False
        assert _eval_batch(_scenario(), {"batches": []})[0] is False
        assert _eval_batch(_scenario(), {"batches": None})[0] is None

    def test_control_evaluator_requires_a_terminal_record(self) -> None:
        completed = {"subagent_id": "s1", "status": "completed", "completed_at": "2026-01-01T00:00:00Z"}
        assert _eval_control(_scenario(), {"record": completed})[0] is True
        running = {"subagent_id": "s1", "status": "running", "completed_at": None}
        assert _eval_control(_scenario(), {"record": running})[0] is False
        assert _eval_control(_scenario(), {})[0] is None

    def test_apply_outcome_maps_evaluator_results_to_pinned_outcomes(self) -> None:
        scenario = next(item for item in scenario_catalogue() if item.key == "turn_capped")

        met = _scenario(key="turn_capped", stop_reasons=["turn_capped"])
        _apply_outcome(scenario, met, {}, [])
        assert met.outcome == "met"
        assert met.expectation_satisfied is True

        unmet = _scenario(key="turn_capped", stop_reasons=[], outcome=None, expectation_satisfied=None)
        _apply_outcome(scenario, unmet, {}, [])
        assert unmet.outcome == "unmet"
        assert unmet.expectation_satisfied is False

        incomplete = _scenario(key="turn_capped", stop_reasons=None, outcome=None, expectation_satisfied=None)
        _apply_outcome(scenario, incomplete, {}, [])
        assert incomplete.outcome == "incomplete"
        assert incomplete.expectation_satisfied is None

    def test_scenario_selection_is_closed_over_known_keys(self) -> None:
        catalogue = scenario_catalogue()
        assert [item.key for item in _select_scenarios(catalogue, "")] == [item.key for item in catalogue]
        selected = _select_scenarios(catalogue, "direct_answer,control_plane_spawn")
        assert [item.key for item in selected] == ["direct_answer", "control_plane_spawn"]
        with pytest.raises(SystemExit):
            _select_scenarios(catalogue, "direct_answer,not_a_scenario")


class TestPlanAcceptanceGates:
    """W1 acceptance gates checkable offline (D3; the offline half of D4)."""

    def test_d3_schema_round_trip_pins_every_key_and_null_honesty(self) -> None:
        payload = to_jsonable(_report([_scenario()]))
        assert validate_report(payload) == []
        assert set(payload) == set(REPORT_FIELDS)
        assert set(payload["scenarios"][0]) == set(SCENARIO_FIELDS)
        assert all(payload["scenarios"][0][name] is None for name in UNMEASURED_SCENARIO_FIELDS)

    def test_d4_gateway_death_is_reported_as_incomplete_with_a_reason(self) -> None:
        golden = to_jsonable(_report([_scenario(run_status="success", delegations=1)]))
        assert validate_report(golden) == []

        scenario = _scenario(key="parallel_fanout_3", outcome="incomplete", detail="gateway unreachable: ConnectError on GET /api/threads/x/runs/y", expectation_satisfied=None)
        died = to_jsonable(_report([scenario], verdict="incomplete", incomplete_reason="parallel_fanout_3: gateway unreachable: ConnectError on GET /api/threads/x/runs/y"))
        assert validate_report(died) == []
        assert report_exit_code(died) == 1
        # The report still carries whatever was measured before the death, and
        # the unmeasured tail stays null — never a fabricated terminal state.
        assert died["scenarios"][0]["run_status"] is None
        assert died["scenarios"][0]["delegations"] is None

    def test_d4_a_target_that_dies_mid_probe_still_yields_a_written_report(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression (2026-10-17): the trailing version read is best-effort.

        A real full-catalogue run hit transport death, crashed on
        ``GET /api/ops/version``, and lost every completed measurement. A dead
        target must instead produce the report the contract promises:
        ``incomplete`` with a reason, on disk, exit 1."""

        async def dead_scenario(client: object, base_url: str, scenario: object) -> None:
            raise probe_module.GatewayUnreachable("connect refused mid-probe")

        async def dead_version(client: object, base_url: str) -> None:
            raise probe_module.GatewayUnreachable("GET /api/ops/version unreachable: ConnectError")

        monkeypatch.setattr(probe_module, "run_scenario", dead_scenario)
        monkeypatch.setattr(probe_module, "_alpha_version", dead_version)

        args = argparse.Namespace(base_url="http://127.0.0.1:8001", token=None, timeout=0.0, out_dir=str(tmp_path))
        payload, out_dir = asyncio.run(probe_module.run_probe(args, [scenario_catalogue()[0]]))

        assert payload["schema_violations"] == []
        assert payload["verdict"] == "incomplete"
        assert payload["alpha_version"] is None
        assert payload["scenarios"][0]["outcome"] == "incomplete"
        assert "gateway unreachable" in payload["scenarios"][0]["detail"]
        assert report_exit_code(payload) == 1
        written = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        assert written["verdict"] == "incomplete"
