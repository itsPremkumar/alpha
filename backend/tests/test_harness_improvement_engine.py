"""The improvement-run engine: the full offline cycle and every honest stop.

The engine's collaborators are injected, so these tests drive the *real* state
machine — checkpointing, probing, contrasting, composing, verifying, reflecting
— against scripted actors with no model call and no network. What they pin:

1. **A run that can complete, completes**: twelve checkpoints, a composed
   successor stored as a new bundle, both deterministic verifications measured,
   the successor deliverable selected, lessons written to the library.
2. **Absence parks, never fabricates.** With no executor the run stops at
   ``BLOCKED`` naming ``HarnessExecutor``, resumes from the same checkpoint once
   one is wired, and a cancelled parked run applies the cancellation at its next
   boundary — the whole cancel path the router keeps reachable while disabled.
3. **Unmeasured stays unknown.** Provider usage the scripted executor does not
   report leaves the run totals ``None`` and the ledger rows ``null``; a
   promise to measure is never rounded to zero.
4. **"No change" is a result.** A flat cycle (identical validators and process
   metrics) retains the baseline, selects the baseline deliverable, writes no
   lesson — and still reaches ``COMPLETED``.
5. **A probe that ran another bundle is a confounder, not a winner.** The
   executor's self-reported bundle id is kept on purpose, so a mismatch turns
   the contrast ``confounded`` and the baseline is retained.
6. **Lessons are retrieved by a later run** through the same library the
   reflection stage wrote, with the secret scrubber between a stored base
   content and the editor's redacted view.

The engine's own refusal texts are asserted by substring: they name the missing
collaborator or the config key, which is what makes the ``BLOCKED`` reason
actionable.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC
from pathlib import Path

import pytest

from alpha.harness_improvement import (
    CandidateScope,
    ComponentDiff,
    ComponentKind,
    ComponentOp,
    ContrastVerdict,
    DeliverableCheck,
    FailureHypothesis,
    HarnessImprovementEngine,
    HarnessImprovementSettings,
    HarnessImprovementStore,
    HarnessProposal,
    ImprovementRun,
    ImprovementRunError,
    ImprovementStage,
    ProbeOutcome,
    PromotionState,
    ScriptedEditor,
    TaskContract,
    TokenUsage,
    ValidatorResult,
    ValidatorStatus,
    compute_task_fingerprint,
    estimate_run_cost,
    qualify_task,
)

# ---------------------------------------------------------------- fixtures

TASK_TEXT = "Create a JSON configuration and confirm it is valid"
TASK_OUTCOME = "write outputs/report.json and verify it parses"


def _task(*, checks: bool = True, outcome: str = TASK_OUTCOME) -> TaskContract:
    return TaskContract(
        task=TASK_TEXT,
        outcome=outcome,
        deliverables=("outputs/report.json",),
        checks=(DeliverableCheck(name="report.parses", path="outputs/report.json", kind="parses_json"),) if checks else (),
        domain_tags=("artifact",),
        task_type="artifact",
        task_fingerprint=compute_task_fingerprint(TASK_TEXT),
    )


def _baseline_components() -> dict[str, tuple[ComponentKind, str]]:
    return {
        "system_prompt/base": (ComponentKind.SYSTEM_PROMPT, "You are Alpha."),
        "tool_guidance/bash": (ComponentKind.TOOL_GUIDANCE, "Use bash carefully."),
    }


class DemoExecutor:
    """A scripted executor that writes real artifacts into its probe workspace.

    ``baseline_fails`` decides which arm of the comparison is broken:
    the baseline writes unparsable JSON, every other role writes valid JSON with
    identical process metrics, so the contrast the engine builds is driven by
    the script rather than by timing.
    """

    def __init__(self, *, baseline_fails: bool = True, probe_bundle_lie: bool = False) -> None:
        self.baseline_fails = baseline_fails
        self.probe_bundle_lie = probe_bundle_lie
        self.calls: list[dict[str, str]] = []

    async def execute(self, *, run_id, role, task, bundle, contents, environment):  # noqa: D102 - protocol
        self.calls.append({"run_id": run_id, "role": role, "bundle_id": bundle.bundle_id})
        failed = role == "baseline" and self.baseline_fails
        report = task.workspace.outputs_dir / "report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("{not-json" if failed else '{"status":"ok"}', encoding="utf-8")
        bundle_id = "sha256:" + "0" * 64 if (self.probe_bundle_lie and role.startswith("probe")) else bundle.bundle_id
        return ProbeOutcome(
            run_id=f"{run_id}:{role}",
            bundle_id=bundle_id,
            candidate_id="",
            task_fingerprint="",
            trace_artifact=f"{task.workspace.root}/logs/trace.json",
            deliverable_refs=("outputs/report.json",),
            validator_results=(ValidatorResult(name="report.parses", status=ValidatorStatus.FAILED if failed else ValidatorStatus.PASSED, detail="scripted"),),
            token_usage=TokenUsage(input_tokens=10, output_tokens=5, cost_usd=None),
            elapsed_ms=5,
            tool_error_count=2 if failed else 0,
            model_turns=3,
            tool_calls=4,
        )


def _invariant_proposal(context) -> HarnessProposal:
    """A valid invariant-scope edit: full new content for the system prompt."""
    return HarnessProposal(
        proposal_id="p-invariant",
        scope=CandidateScope.INVARIANT,
        base_bundle_id=context.base_bundle.bundle_id,
        hypothesis=FailureHypothesis(
            summary="reopen artifacts before announcing completion",
            trace_event_ids=("baseline.completed",),
            expected_behavioral_change="the artifact parses at completion time",
            falsification_condition="the artifact is still malformed when the run finishes",
        ),
        component_diffs=(
            ComponentDiff(
                op=ComponentOp.MODIFY,
                kind=ComponentKind.SYSTEM_PROMPT,
                component_id="system_prompt/base",
                content="Always reopen and parse each deliverable before announcing completion.",
                rationale="baseline announced completion without verifying the file",
                evidence_event_ids=("baseline.completed",),
            ),
        ),
    )


def _domain_proposal(context) -> HarnessProposal:
    """A valid task/domain-scope edit: a new on-demand skill component."""
    return HarnessProposal(
        proposal_id="p-domain",
        scope=CandidateScope.TASK_OR_DOMAIN,
        base_bundle_id=context.base_bundle.bundle_id,
        hypothesis=FailureHypothesis(
            summary="add an on-demand artifact verification skill",
            trace_event_ids=("baseline.completed",),
            expected_behavioral_change="artifact tasks verify their JSON output",
            falsification_condition="the successor still writes unparsable JSON",
        ),
        component_diffs=(
            ComponentDiff(
                op=ComponentOp.ADD,
                kind=ComponentKind.SKILL,
                component_id="skills/artifact-verification",
                content="# Artifact verification\nReopen and parse each deliverable after writing it.\n",
                rationale="deterministic reopen check",
                evidence_event_ids=("baseline.completed",),
            ),
        ),
    )


def _editor(*, invariant=_invariant_proposal, domain=_domain_proposal) -> ScriptedEditor:
    return ScriptedEditor(proposals={CandidateScope.INVARIANT: invariant, CandidateScope.TASK_OR_DOMAIN: domain})


def _engine(tmp_path: Path, *, executor: DemoExecutor | None | object = "demo", editor: ScriptedEditor | None | object = "default", baseline: bool = True, **settings) -> HarnessImprovementEngine:
    """An engine bound to ``tmp_path``; ``executor=None`` pins the absent posture."""
    if executor == "demo":
        executor = DemoExecutor()
    if editor == "default":
        editor = _editor()
    payload: dict[str, object] = {"enabled": True, "max_model_calls": 64}
    payload.update(settings)
    return HarnessImprovementEngine(
        store=HarnessImprovementStore(tmp_path / "hi"),
        settings=HarnessImprovementSettings(**payload),
        executor=executor if executor != "absent" else None,
        editor=editor if editor != "absent" else None,
        baseline_provider=_baseline_components if baseline else None,
    )


def _run(engine: HarnessImprovementEngine, *, run_id: str = "run-a", task: TaskContract | None = None) -> ImprovementRun:
    return asyncio.run(engine.start(task=task or _task(), run_id=run_id, created_by="operator"))


# ---------------------------------------------------------------- full cycle


class TestFullOfflineCycle:
    def test_a_full_cycle_completes_and_selects_the_composed_successor(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        run = _run(engine)

        assert run.stage is ImprovementStage.COMPLETED
        assert len(run.checkpoints) == 12, sorted(run.checkpoints)
        assert run.next_incomplete_stage() is ImprovementStage.COMPLETED
        assert run.terminal and not run.resumable
        assert run.error is None

        # Two proposals, two accepted candidates, two probes, one successor run.
        assert [p.proposal_id for p in run.proposals] == ["p-invariant", "p-domain"]
        assert [row["accepted"] for row in run.validations] == [True, True]
        assert sorted(o.candidate_id for o in run.probe_outcomes) == ["baseline", "p-domain", "p-invariant", "successor"]
        assert [entry["role"] for entry in engine.executor.calls] == ["baseline", "probe_invariant", "probe_task_or_domain", "successor"]

        # Both contrasts are positive: the baseline's failed check passed, with
        # process metrics improved and no confounders.
        assert [str(c.verdict) for c in run.contrasts] == ["positive", "positive"]
        assert all(c.targeted_failure_resolved is True for c in run.contrasts)
        assert all("confounded" not in c.notes for c in run.contrasts)

        # The composer adopted both edits under a new child bundle; the base is
        # untouched and nothing was promoted.
        assert run.composition is not None and not run.composition.retained_baseline
        assert len(run.composition.adopted) == 2
        assert run.successor_bundle_id is not None
        successor = engine.store.load_bundle(run.successor_bundle_id)
        assert successor is not None
        assert successor.parent_bundle_id == run.base_bundle_id
        assert successor.promotion_state is PromotionState.DRAFT
        base = engine.store.load_bundle(run.base_bundle_id)
        assert base is not None and base.promotion_state is PromotionState.ACTIVE
        assert engine.store.load_active() is None, "bootstrap must not move the active pointer"
        skill_ref = successor.component("skills/artifact-verification")
        assert skill_ref is not None
        skill_content = engine.store.get_blob(skill_ref.sha256)
        assert skill_content is not None and "Artifact verification" in skill_content

        # Provenance: the engine stamps the producing run on every stored
        # candidate and on the successor, because the promotion router
        # reconstructs evidence from the run record named here. The stamp is
        # outside the bundle digest, so candidate ids above are unchanged.
        assert successor.source_run_id == run.run_id
        accepted_ids = [row["candidate_bundle_id"] for row in run.validations if row["accepted"]]
        assert accepted_ids, "the full cycle accepted at least one candidate"
        for candidate_id in accepted_ids:
            candidate = engine.store.load_bundle(candidate_id)
            assert candidate is not None and candidate.source_run_id == run.run_id

        # Deliverable: the successor's re-measured checks passed, so it wins.
        assert run.deliverable is not None
        assert run.deliverable["selected"] == "successor"
        assert run.deliverable["bundle_id"] == run.successor_bundle_id
        assert [row["status"] for row in run.deliverable["successor_validators"]] == ["passed"]
        assert [row["status"] for row in run.deliverable["baseline_validators"]] == ["failed"]
        assert run.deliverable["successor_supported"] is True

        # ONE BENEFIT LESSON PER POSITIVE CONTRAST, none promoted by the engine.
        assert len(run.experiences) == 2
        for experience_id in run.experiences:
            metadata, markdown = engine.store.load_experience(experience_id)
            assert metadata["finding"] == "benefit"
            assert metadata["status"] == "proposed"
            assert metadata["evidence_refs"]
            assert "## Evidence" in markdown
        assert any("promotion remains operator-gated" in note for note in run.notes)

    def test_the_run_measured_exactly_the_model_spending_it_did(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        run = _run(engine)

        spent = run.budget_state["spent"]
        # baseline + two editor calls + two probes + successor, exactly the
        # pre-run shape estimate_run_cost projects for a two-candidate cycle.
        assert spent["model_calls"] == 6
        assert spent["model_calls"] == estimate_run_cost(candidates=2, mode="sequential")["max_model_executions"]
        assert spent["tool_calls"] == 16
        assert spent["model_turns"] == 12
        assert spent["elapsed_ms"] == 20

        # The scripted executor never reports cost or tokens for the editor
        # stages; the totals stay unknown rather than summing a partial figure.
        assert spent["cost_usd"] is None
        assert spent["input_tokens"] is None
        assert spent["output_tokens"] is None
        assert any(row.endswith(":cost_usd") for row in run.budget_state["unmeasured"])
        assert "single-process JSON/JSONL" in run.budget_state["durability"]

        ledger = engine.store.read_ledger()
        assert ledger.complete
        assert [row["stage"] for row in ledger.items] == ["baseline_running", "proposing_invariant", "proposing_task_or_domain", "probe_invariant", "probe_task_or_domain", "successor_running"]
        assert all(row["cost_usd"] is None for row in ledger.items), "an unreported dimension is null in the ledger, never zero"
        assert all(row["ceilings"]["max_model_calls"] == 64 for row in ledger.items)

    def test_the_run_record_round_trips_through_the_store(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        run = _run(engine)

        payload = engine.store.load_run("run-a")
        assert payload is not None
        reloaded = ImprovementRun.from_dict(payload)
        assert reloaded.stage is ImprovementStage.COMPLETED
        assert len(reloaded.checkpoints) == 12
        assert reloaded.successor_bundle_id == run.successor_bundle_id

        status = engine.status("run-a")
        assert status is not None
        assert status["run"]["stage"] == "completed"
        assert status["resumable"] is False
        assert status["next_stage"] == "completed"
        assert status["unreadable_event_lines"] == []
        assert status["durability"].startswith("single-process JSON/JSONL")
        assert status["events"][-1]["event"] == "run.completed"
        assert engine.status("run-missing") is None

    def test_a_later_run_retrieves_the_lessons_the_library_stored(self, tmp_path: Path) -> None:
        editor = _editor()
        engine = _engine(tmp_path, editor=editor)
        _run(engine, run_id="run-a")
        assert all(not context["payload"]["experience_summaries"] for context in editor.contexts), "the first run has an empty library"

        _run(engine, run_id="run-b")
        second_run_contexts = editor.contexts[2:]
        assert second_run_contexts, "the second run must have asked the editor for proposals"
        summaries = second_run_contexts[0]["payload"]["experience_summaries"]
        assert summaries, "the library entries from run-a must be retrievable by run-b"
        assert all(row["finding"] == "benefit" for row in summaries)
        assert all("score" in row and "reasons" in row for row in summaries)

    def test_the_editor_never_receives_a_raw_secret(self, tmp_path: Path) -> None:
        secret = "sk-abcdefgh12345678"
        editor = _editor()
        engine = _engine(tmp_path, editor=editor)
        engine.baseline_provider = lambda: {"system_prompt/base": (ComponentKind.SYSTEM_PROMPT, f"deploy key {secret} stays out of prompts")}
        _run(engine, run_id="run-secret")

        findings = [finding for context in editor.contexts for finding in context["redactions"]]
        assert any(finding["kind"] == "api_key" for finding in findings)
        serialized = json.dumps(editor.contexts[-1]["payload"])
        assert secret not in serialized
        assert "[REDACTED:api_key]" in serialized


# ---------------------------------------------------------------- parked runs


class TestParkedAndCancelled:
    def test_no_executor_parks_at_blocked_naming_the_collaborator_then_resumes(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path, executor="absent", editor="absent")
        run = _run(engine)
        assert run.stage is ImprovementStage.BLOCKED
        assert "HarnessExecutor" in run.blocked_reason
        assert run.resumable
        assert run.next_incomplete_stage() is ImprovementStage.BASELINE_RUNNING
        assert run.probe_outcomes == [], "a parked run must not fabricate a baseline outcome"

        engine.executor = DemoExecutor()
        engine.editor = _editor()
        resumed = asyncio.run(engine.resume("run-a"))
        assert resumed.stage is ImprovementStage.COMPLETED
        assert resumed.blocked_reason != ""

    def test_no_editor_parks_after_the_baseline_was_measured(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path, editor="absent")
        run = _run(engine)
        assert run.stage is ImprovementStage.BLOCKED
        assert "HarnessEditor" in run.blocked_reason
        assert run.next_incomplete_stage() is ImprovementStage.PROPOSING
        assert [outcome.candidate_id for outcome in run.probe_outcomes] == ["baseline"], "the measured baseline survives the park"

    def test_a_cancelled_parked_run_applies_cancellation_on_resume(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path, executor="absent", editor="absent")
        parked = _run(engine)
        assert parked.stage is ImprovementStage.BLOCKED

        requested = engine.cancel("run-a", reason="operator stopped it")
        assert requested.cancel_requested is True
        assert requested.cancel_reason == "operator stopped it"
        assert requested.stage is ImprovementStage.BLOCKED, "cancel is a request, applied at the next boundary"

        applied = asyncio.run(engine.resume("run-a"))
        assert applied.stage is ImprovementStage.CANCELLED
        assert not applied.resumable
        with pytest.raises(ImprovementRunError, match="cannot be resumed"):
            asyncio.run(engine.resume("run-a"))
        with pytest.raises(ImprovementRunError, match="already"):
            engine.cancel("run-a")

    def test_resuming_a_terminal_run_is_refused(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _run(engine)
        with pytest.raises(ImprovementRunError, match="cannot be resumed"):
            asyncio.run(engine.resume("run-a"))
        with pytest.raises(ImprovementRunError, match="does not exist"):
            asyncio.run(engine.resume("run-missing"))

    def test_resuming_a_blocked_run_without_the_collaborator_parks_again(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path, executor="absent", editor="absent")
        _run(engine)
        again = asyncio.run(engine.resume("run-a"))
        assert again.stage is ImprovementStage.BLOCKED, "resume must not skip the stage it parked at"
        assert "HarnessExecutor" in again.blocked_reason


# ---------------------------------------------------------------- refusals


class TestStartRefusals:
    def test_a_disabled_feature_refuses_by_name(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path, enabled=False)
        with pytest.raises(ImprovementRunError, match="harness_improvement is disabled"):
            _run(engine)

    def test_an_unimplemented_mode_is_refused_not_degraded(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path, mode="distributed")
        with pytest.raises(ImprovementRunError, match="is not implemented"):
            _run(engine)

    def test_no_baseline_and_no_provider_refuses_instead_of_inventing_one(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path, baseline=False)
        with pytest.raises(ImprovementRunError, match="refusing to invent a baseline"):
            _run(engine)

    def test_an_inactive_task_is_refused_with_the_reason(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with pytest.raises(ImprovementRunError, match="no requested outcome"):
            _run(engine, task=_task(outcome="   "))


class TestQualifyTask:
    def test_a_task_without_an_outcome_is_refused(self) -> None:
        assert qualify_task(TaskContract(task="t", outcome=" ")) == "the task contract states no requested outcome"

    def test_a_task_without_checks_or_deliverables_is_unfalsifiable(self) -> None:
        refusal = qualify_task(TaskContract(task="t", outcome="decide something"))
        assert refusal is not None and "unfalsifiable" in refusal

    def test_checks_alone_are_enough(self) -> None:
        task = TaskContract(task="t", outcome="write it", checks=(DeliverableCheck(name="c", path="outputs/x", kind="exists"),))
        assert qualify_task(task) is None

    def test_declared_uncertainty_about_observability_is_refused(self) -> None:
        task = TaskContract(task="t", outcome="write it", deliverables=("outputs/x",), uncertainties=("the result has No Observable acceptance criteria",))
        refusal = qualify_task(task)
        assert refusal is not None and "no observable" in refusal.lower()

    def test_a_qualified_task_returns_none(self) -> None:
        assert qualify_task(_task()) is None


# ---------------------------------------------------------------- honest "no change"


class TestNoChangeCycle:
    def test_a_flat_cycle_retains_the_baseline_and_still_completes(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path, executor=DemoExecutor(baseline_fails=False))
        run = _run(engine)

        assert run.stage is ImprovementStage.COMPLETED
        assert len(run.checkpoints) == 12
        # Both scopes are probed; with nothing failing under the baseline both
        # contrasts are neutral and neither edit is adopted.
        assert [str(c.verdict) for c in run.contrasts] == ["neutral", "neutral"]
        assert all(c.targeted_failure_resolved is None for c in run.contrasts)
        assert all(any("no targeted failure validator was named" in note for note in c.uncertainty) for c in run.contrasts)

        assert run.composition is not None and run.composition.retained_baseline is True
        assert run.composition.successor_bundle_id == run.base_bundle_id
        assert run.successor_bundle_id is None
        assert [entry["role"] for entry in engine.executor.calls] == ["baseline", "probe_invariant", "probe_task_or_domain"], "a retained baseline runs no successor"

        assert run.deliverable is not None
        assert run.deliverable["selected"] == "baseline"
        assert run.deliverable["reason"] == "no supported successor deliverable was available"
        assert run.deliverable["successor_validators"] is None
        assert run.deliverable["successor_supported"] is False

        assert run.experiences == []
        assert any("experience not written" in note for note in run.notes)
        assert any("no successor run" in note for note in run.notes)

    def test_a_lying_probe_bundle_id_confounds_instead_of_being_corrected(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path, executor=DemoExecutor(probe_bundle_lie=True))
        run = _run(engine)

        assert run.stage is ImprovementStage.COMPLETED
        contrast = next(c for c in run.contrasts if c.candidate_id == "p-invariant")
        assert contrast.verdict is ContrastVerdict.CONFOUNDED
        assert "candidate probe ran bundle" in contrast.notes or any("expected candidate bundle" in note for note in contrast.uncertainty) or "expected candidate bundle" in contrast.notes
        assert run.composition is not None and run.composition.retained_baseline
        assert run.experiences == []
        assert run.deliverable is not None and run.deliverable["selected"] == "baseline"


# ---------------------------------------------------------------- engine plumbing


class TestEnginePlumbing:
    def test_the_first_run_bootstraps_the_baseline_from_the_provider(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        run = _run(engine)
        base = engine.store.load_bundle(run.base_bundle_id)
        assert base is not None
        assert base.created_by == "baseline_provider"
        assert base.promotion_state is PromotionState.ACTIVE
        assert base.parent_bundle_id is None
        assert {ref.component_id for ref in base.components} == {"system_prompt/base", "tool_guidance/bash"}

        # A second run reuses the same content-addressed baseline rather than
        # minting a new one.
        second = _run(engine, run_id="run-b")
        assert second.base_bundle_id == run.base_bundle_id

    def test_config_hash_tracks_the_settings_not_the_clock(self, tmp_path: Path) -> None:
        first = HarnessImprovementSettings(enabled=True, max_model_calls=64)
        same = HarnessImprovementSettings(enabled=True, max_model_calls=64)
        other = HarnessImprovementSettings(enabled=True, max_model_calls=65)
        assert first.config_hash() == same.config_hash()
        assert first.config_hash() != other.config_hash()
        assert first.config_hash().startswith("sha256:")

    def test_settings_bounds_travel_into_the_patch_policy(self) -> None:
        settings = HarnessImprovementSettings(max_diff_files=3, max_changed_bytes=100, allow_candidate_middleware_changes=True, allow_candidate_guardrail_policy_changes=True)
        policy = settings.patch_policy()
        assert policy.max_diff_files == 3
        assert policy.max_changed_bytes == 100
        assert policy.allow_middleware_changes is True
        assert policy.allow_guardrail_policy_changes is True
        ceilings = settings.ceilings()
        assert ceilings.max_model_calls == settings.max_model_calls
        assert ceilings.max_wall_time_seconds == settings.max_wall_time_seconds


def test_improvement_runs_record_utc_timestamps(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    run = _run(engine)
    created = run.created_at
    assert created.endswith("Z")
    # Timestamps are durable strings; parsing them keeps a future format change
    # from silently writing a local-time value.
    from datetime import datetime

    parsed = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert parsed.year >= 2024
