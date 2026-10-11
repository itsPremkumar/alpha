"""GRO runner: one bounded, durable group-relative orchestration run.

``run_with_gro`` is the whole phase pipeline of the plan (section 9) in one
synchronous call: policy decision -> contract -> strategy deal -> bounded
candidate execution -> hard-gate verification -> relative ranking -> selection
(or a bounded failure-directed repair round) -> persistence.

Load-bearing honesty rules enforced here:

- A run is never "verified" by being the best of a bad group: every *required*
  gate must pass for the winner (``alpha.gro.evaluation``), and with
  ``require_final_verification`` the winner's gates are re-checked against the
  live workspace before it is reported — a re-check failure demotes the run to
  ``no_acceptable_candidate``, never to a quiet success.
- With no acceptance criteria declared, a winner is still reported but always
  labelled ``UNVERIFIED`` (selection rested on execution evidence and cost
  only).
- Every candidate gets an outcome record: failed, timed-out, fenced-late and
  never-started candidates are recorded as such, because omission would read
  as success to any later ranker.
- An unexpected runner error moves the run to ``FAILED`` with the reason, and
  says so — it never returns a half-run as an answer.

The verifier is injectable (``verifier=``); the default reuses the existing
deterministic acceptance checker against each candidate's harvested bash
evidence. The runner itself never scores — scoring and eligibility live in
``alpha.gro.evaluation``.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.gro.adapters import ExecutionAdapter
from alpha.gro.config import GroConfig
from alpha.gro.coordinator import GroCoordinator
from alpha.gro.evaluation import GATE_EXECUTION, evaluate_candidate, rank_group, select_candidate
from alpha.gro.models import (
    CandidateEvaluation,
    CandidateOutcome,
    CandidateSpec,
    CandidateStatus,
    GateStatus,
    GroBudget,
    GroRun,
    GroRunState,
    RunMode,
    SelectionResult,
    TaskContract,
    TaskType,
)
from alpha.gro.policy import decide_mode
from alpha.gro.strategies import REPAIR_STRATEGY, select_strategies, strategies_by_id

logger = logging.getLogger(__name__)

Verifier = Callable[[TaskContract, CandidateOutcome], Mapping[str, Any] | None]
EventSink = Callable[[dict[str, Any]], None]

_MAX_GOAL_CHARS = 8000
_MAX_CRITERIA = 50
_MAX_CRITERION_CHARS = 2000
_MAX_REPORT_OUTPUT_CHARS = 6000
_REPAIR_CONTEXT_TAIL = 1500


class GroDisabledError(RuntimeError):
    """Raised when the engine is asked to run while ``gro.enabled`` is false."""


def default_acceptance_verifier(contract: TaskContract, outcome: CandidateOutcome) -> Mapping[str, Any] | None:
    """Verify one candidate with the existing deterministic acceptance checker.

    The checker reads the candidate's harvested bash executions (the same
    evidence the ``task`` tool's checklist uses). Criteria it cannot decide
    come back unchecked, which keeps their gate ``UNKNOWN`` — never a pass.
    File-scoped criteria have no sandbox runtime in this path and therefore
    degrade to unchecked rather than being guessed.
    """

    if not contract.acceptance_criteria:
        return None
    from alpha.subagents.acceptance_checks import check_acceptance_criteria

    verdict = check_acceptance_criteria(list(contract.acceptance_criteria), bash_executions=list(outcome.bash_executions) or None)
    return verdict


@dataclass
class GroReport:
    """The caller-facing result of one orchestration run."""

    run: GroRun
    outcome: str  # selected | no_acceptable_candidate | partial_result | failed | needs_human
    winner_text: str = ""
    winner_candidate_id: str | None = None
    partial_text: str = ""
    partial_candidate_id: str | None = None
    verified: bool = False
    verified_criteria_count: int = 0
    persisted: bool = False
    error: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    not_started_count: int = 0

    @property
    def run_id(self) -> str:
        return self.run.run_id

    @property
    def state(self) -> GroRunState:
        return self.run.state

    def to_dict(self) -> dict[str, Any]:
        selection = self.run.selection.to_dict() if self.run.selection else None
        return {
            "run_id": self.run_id,
            "state": self.run.state.value,
            "outcome": self.outcome,
            "mode": self.run.mode.value,
            "task_type": self.run.contract.task_type.value,
            "risk_tier": self.run.contract.risk_tier.value,
            "verified": self.verified,
            "verified_criteria_count": self.verified_criteria_count,
            "winner_candidate_id": self.winner_candidate_id,
            "partial_candidate_id": self.partial_candidate_id,
            "persisted": self.persisted,
            "error": self.error,
            "not_started_count": self.not_started_count,
            "selection": selection,
            "decision": self.run.decision,
            "repair_round": self.run.repair_round,
            "evaluations": {cid: evaluation.to_dict() for cid, evaluation in self.run.evaluations.items()},
            "budget": self.run.budget.to_dict(),
        }

    def markdown(self) -> str:
        """Render a bounded, honest report for the model-facing tool text."""

        run = self.run
        contract = run.contract
        lines: list[str] = [f"### GRO run `{run.run_id}` — **{self.outcome}** (state: `{run.state.value}`)"]
        lines.append(
            f"- Task type: `{contract.task_type.value}` · risk tier: `{contract.risk_tier.value}` · mode: `{run.mode.value}` · candidates: {len(run.candidate_specs)} · repair rounds: {run.repair_round}/{run.budget.max_repair_rounds}"
        )
        if run.decision.get("reasons"):
            lines.append(f"- Policy reasons: {', '.join(str(r) for r in run.decision['reasons'])}")
        lines.append(f"- Criteria declared: {len(contract.acceptance_criteria)}")

        if run.evaluations:
            lines.append("")
            lines.append("| candidate | strategy | eligible | score | rel. rank | gates |")
            lines.append("|---|---|---|---|---|---|")
            ordered = sorted(run.evaluations.values(), key=lambda e: (e.relative_rank or 99, e.candidate_id))
            for evaluation in ordered:
                gates = ",".join(f"{g.name.split('_')[0]}:{g.status.value}" for g in evaluation.gates)
                score = "-" if evaluation.absolute_score is None else f"{evaluation.absolute_score:.3f}"
                rank = str(evaluation.relative_rank) if evaluation.relative_rank is not None else "-"
                lines.append(f"| {evaluation.candidate_id} | {_strategy_id(run, evaluation.candidate_id)} | {'yes' if evaluation.eligible else 'no'} | {score} | {rank} | {gates} |")

        if self.outcome == "selected" and run.selection:
            lines.append("")
            lines.append(f"- **Winner: `{self.winner_candidate_id}`** — {run.selection.reason}")
            if self.verified:
                lines.append(f"- Verification: **passed** all declared acceptance criteria ({self.verified_criteria_count} checked against evidence).")
            else:
                lines.append("- Verification: **UNVERIFIED** — no acceptance criteria were declared, so selection rested on execution evidence and cost only.")
            lines.append("")
            lines.append("#### Selected candidate output")
            lines.append(_fenced(_bounded(self.winner_text, _MAX_REPORT_OUTPUT_CHARS)))
        elif self.outcome == "partial_result":
            lines.append("")
            lines.append(f"- No candidate passed every required gate. Returning the closest attempt `{self.partial_candidate_id}` as an **UNVERIFIED partial result**.")
            if run.selection:
                lines.append(f"- Reason: {run.selection.reason}")
            lines.append("")
            lines.append("#### Partial (unverified) output")
            lines.append(_fenced(_bounded(self.partial_text, _MAX_REPORT_OUTPUT_CHARS)))
        elif self.outcome == "no_acceptable_candidate":
            lines.append("")
            lines.append("- **No winner:** no candidate passed every required gate.")
            if run.selection:
                lines.append(f"- Reason: {run.selection.reason}")
                lines.append(f"- Repair rounds used: {run.repair_round}/{run.budget.max_repair_rounds}.")
        elif self.outcome == "needs_human":
            lines.append("")
            lines.append("- Blocked on human approval before any candidate may execute (external side effects). No work was run.")
        elif self.outcome == "in_progress":
            lines.append("")
            lines.append(f"- Run has not completed (state `{run.state.value}`); no winner exists yet and none should be assumed.")
        elif self.outcome == "failed":
            lines.append("")
            lines.append(f"- **Run failed:** {self.error or 'no detail recorded'}")

        if self.not_started_count:
            lines.append(f"- {self.not_started_count} candidate(s) were never started (early stop or deadline) and hold no result — they are not part of the ranking.")
        if run.is_terminal is False and self.outcome != "needs_human":
            lines.append(f"- Note: run is in non-terminal state `{run.state.value}`.")
        if not self.persisted:
            lines.append("- **Warning: this run could not be persisted** — its state file was not written (see store error).")
        warnings = sorted({code for evaluation in run.evaluations.values() for code in evaluation.reason_codes if code.startswith(("unchecked_", "gate_"))})
        if warnings:
            lines.append(f"- Gate/reason detail: {'; '.join(warnings[:8])}")
        return "\n".join(lines)


def _strategy_id(run: GroRun, candidate_id: str) -> str:
    for spec in run.candidate_specs:
        if spec.candidate_id == candidate_id:
            return spec.strategy.strategy_id
    return "?"


def _bounded(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n… (truncated)"


def _fenced(text: str) -> str:
    return f"```\n{text}\n```" if text.strip() else "```\n(no output text recorded)\n```"


def _normalize_criteria(acceptance_criteria: Sequence[str]) -> tuple[str, ...]:
    criteria = [str(c).strip() for c in acceptance_criteria if str(c).strip()]
    if len(criteria) > _MAX_CRITERIA:
        raise ValueError(f"refusing {len(criteria)} acceptance criteria: the ceiling is {_MAX_CRITERIA} (bounds are refused, never clamped)")
    for criterion in criteria:
        if len(criterion) > _MAX_CRITERION_CHARS:
            raise ValueError(f"refusing a {len(criterion)}-character acceptance criterion: the ceiling is {_MAX_CRITERION_CHARS} characters")
    return tuple(criteria)


def run_with_gro(
    goal: str,
    *,
    adapter: ExecutionAdapter,
    acceptance_criteria: Sequence[str] = (),
    constraints: Sequence[str] = (),
    task_type: TaskType | None = None,
    candidate_count: int | None = None,
    strategy_ids: Sequence[str] = (),
    config: GroConfig | None = None,
    owner_id: str = "",
    thread_id: str = "",
    run_id: str | None = None,
    store: Any | None = None,
    verifier: Verifier | None = None,
    on_event: EventSink | None = None,
    now: Callable[[], float] = time.time,
) -> GroReport:
    """Execute one group-relative orchestration run and return its report."""

    cfg = config or GroConfig()
    if not cfg.enabled:
        raise GroDisabledError("group-relative orchestration is disabled (set `gro.enabled: true` in config.yaml)")
    normalized_goal = " ".join(str(goal).split())
    if not normalized_goal:
        raise ValueError("goal is required for a GRO run")
    if len(normalized_goal) > _MAX_GOAL_CHARS:
        raise ValueError(f"refusing a {len(normalized_goal)}-character goal: the ceiling is {_MAX_GOAL_CHARS} characters")
    criteria = _normalize_criteria(acceptance_criteria)

    events: list[dict[str, Any]] = []
    run_holder: dict[str, GroRun] = {}

    def emit(event_type: str, **fields: Any) -> None:
        payload = {"type": event_type, **fields}
        events.append(payload)
        if on_event is not None:
            try:
                on_event(payload)
            except Exception:  # noqa: BLE001 - a observer must not break the run
                logger.warning("GRO event observer raised", exc_info=True)
        run = run_holder.get("run")
        if run is not None and store is not None:
            store.append_event(owner_id=run.owner_id, run_id=run.run_id, event=payload)

    # --- Phase A/B: policy decision ------------------------------------
    decision = decide_mode(normalized_goal, criteria, config=cfg, task_type=task_type, candidate_count=candidate_count)
    resolved_run_id = run_id or f"gro_{uuid.uuid4().hex[:12]}"
    contract = TaskContract(
        run_id=resolved_run_id,
        goal=normalized_goal,
        task_type=decision.task_type,
        risk_tier=decision.risk_tier,
        acceptance_criteria=criteria,
        constraints=tuple(str(c).strip() for c in constraints if str(c).strip()),
        task_id=thread_id,
    )
    group_size = max(1, decision.group_size)
    # ``max_candidates`` is the run-wide lane ceiling: the planned group plus
    # the bounded repair rounds. The operator's per-task ceiling bounds the
    # initial group; repairs are bounded separately by ``max_repair_rounds``.
    budget = GroBudget(
        max_candidates=group_size + cfg.max_repair_rounds,
        max_active=min(cfg.max_active_candidates_global, max(group_size, 1)),
        candidate_timeout_seconds=cfg.candidate_timeout_seconds,
        run_timeout_seconds=cfg.run_timeout_seconds,
        max_repair_rounds=cfg.max_repair_rounds,
    )
    run = GroRun.new(
        run_id=resolved_run_id,
        owner_id=owner_id,
        thread_id=thread_id,
        contract=contract,
        budget=budget,
        mode=decision.mode,
    )
    run.decision = decision.to_dict()
    run_holder["run"] = run
    emit("run.started", goal=normalized_goal[:200], mode=decision.mode.value, tier=decision.risk_tier.value, group_size=group_size)

    run.transition(GroRunState.NORMALIZED, reason="goal and acceptance criteria normalized")
    run.transition(
        GroRunState.POLICY_DECIDED,
        reason=f"mode={decision.mode.value} tier={decision.risk_tier.value} reasons={','.join(decision.reasons)}",
    )

    if decision.mode is RunMode.HUMAN_APPROVAL_FIRST:
        run.transition(GroRunState.NEEDS_HUMAN, reason="external side effects require approval before any candidate executes")
        persisted = _persist(run, store)
        emit("run.needs_human")
        return GroReport(run=run, outcome="needs_human", persisted=persisted, events=events)

    try:
        _execute_phases(run, cfg, budget, adapter, decision, strategy_ids, verifier or default_acceptance_verifier, emit, now)
    except GroDisabledError:
        raise
    except Exception as exc:  # noqa: BLE001 - disclose as a failed run, never a half-run answer
        if not run.is_terminal:
            try:
                run.transition(GroRunState.FAILED, reason=f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001 - a failed transition must not hide the original error
                logger.warning("GRO run %s could not transition to FAILED", run.run_id, exc_info=True)
        emit("run.failed", error=f"{type(exc).__name__}: {exc}")
        persisted = _persist(run, store)
        logger.warning("GRO run %s failed", run.run_id, exc_info=True)
        return GroReport(run=run, outcome="failed", error=f"{type(exc).__name__}: {exc}", persisted=persisted, events=events)

    return _finalize_report(run, cfg, store, verifier or default_acceptance_verifier, emit, events)


def _execute_phases(
    run: GroRun,
    cfg: GroConfig,
    budget: GroBudget,
    adapter: ExecutionAdapter,
    decision: Any,
    strategy_ids: Sequence[str],
    verifier: Verifier,
    emit: Callable[..., None],
    now: Callable[[], float],
) -> None:
    """Phases C-F: deal strategies, execute bounded waves, verify, rank, repair."""

    if strategy_ids:
        strategies = strategies_by_id(strategy_ids)
        if len(strategies) > cfg.max_candidates_per_task:
            raise ValueError(f"refusing {len(strategies)} explicit strategies: the ceiling is {cfg.max_candidates_per_task}")
    else:
        strategies = select_strategies(decision.task_type, decision.group_size)

    specs = [
        CandidateSpec(
            candidate_id=f"cand_{index:02d}",
            run_id=run.run_id,
            strategy=strategy,
            objective=run.contract.goal,
            acceptance_criteria=run.contract.acceptance_criteria,
        )
        for index, strategy in enumerate(strategies, start=1)
    ]
    run.candidate_specs.extend(specs)
    run.transition(GroRunState.GROUP_CREATED if len(specs) > 1 else GroRunState.SINGLE_ATTEMPT, reason=f"dealt {len(specs)} distinct strategies: {', '.join(s.strategy_id for s in strategies)}")
    run.transition(GroRunState.READY, reason="candidates planned")

    coordinator = GroCoordinator(
        max_active=budget.max_active,
        candidate_timeout_seconds=budget.candidate_timeout_seconds,
        run_timeout_seconds=budget.run_timeout_seconds,
        on_event=emit,
    )
    run_started = now()
    run_deadline = run_started + budget.run_timeout_seconds
    run.transition(GroRunState.RUNNING, reason=f"{len(specs)} candidate(s), max_active={budget.max_active}")

    chunks = [specs[i : i + budget.max_active] for i in range(0, len(specs), budget.max_active)]
    for wave_index, chunk in enumerate(chunks, start=1):
        if now() >= run_deadline:
            for spec in chunk:
                run.outcomes[spec.candidate_id] = CandidateOutcome(
                    candidate_id=spec.candidate_id,
                    status=CandidateStatus.NOT_STARTED,
                    error="the run deadline arrived before this candidate was dispatched; it never ran",
                )
            emit("run.deadline_reached", at_wave=wave_index)
            break
        emit("wave.started", wave=wave_index, of=len(chunks), candidates=[spec.candidate_id for spec in chunk])
        outcomes = coordinator.execute_group(chunk, adapter.run, started_at=run_started)
        run.outcomes.update(outcomes)
        for candidate_id, outcome in outcomes.items():
            verdict = _safe_verifier(verifier, run.contract, outcome)
            run.evaluations[candidate_id] = evaluate_candidate(
                run.contract,
                outcome,
                verdict,
                candidate_timeout_seconds=budget.candidate_timeout_seconds,
            )
        emit("wave.completed", wave=wave_index, statuses={cid: o.status.value for cid, o in outcomes.items()})
        if cfg.early_stop_on_verified_success and wave_index < len(chunks) and any(e.eligible for e in run.evaluations.values()):
            remaining = [spec for chunk_left in chunks[wave_index:] for spec in chunk_left]
            for spec in remaining:
                run.outcomes[spec.candidate_id] = CandidateOutcome(
                    candidate_id=spec.candidate_id,
                    status=CandidateStatus.NOT_STARTED,
                    error="early stop: an eligible candidate already exists (gro.early_stop_on_verified_success); this candidate was never dispatched",
                )
            emit("run.early_stopped", skipped=[spec.candidate_id for spec in remaining])
            break

    # --- Phase E: verify every executed candidate, then rank ------------
    for candidate_id, outcome in run.outcomes.items():
        if candidate_id in run.evaluations:
            continue
        verdict = _safe_verifier(verifier, run.contract, outcome)
        run.evaluations[candidate_id] = evaluate_candidate(
            run.contract,
            outcome,
            verdict,
            candidate_timeout_seconds=budget.candidate_timeout_seconds,
        )
    run.transition(GroRunState.VERIFYING, reason="execution complete; verifying candidates against the contract")
    rank_group(list(run.evaluations.values()))
    selection = select_candidate(list(run.evaluations.values()))
    run.selection = selection
    run.transition(GroRunState.RANKED, reason=f"ranked {sum(1 for e in run.evaluations.values() if e.eligible)} eligible candidate(s)")
    run.transition(
        GroRunState.SELECTED if selection.outcome == "selected" else GroRunState.NO_ACCEPTABLE_CANDIDATE,
        reason=selection.reason,
    )
    emit(
        "selection.completed",
        outcome=selection.outcome,
        selected=selection.selected_candidate_id,
        ranking=selection.ranking,
        repairable=selection.repairable,
    )

    # --- Phase F: bounded failure-directed repair ------------------------
    while run.selection is not None and run.selection.outcome == "no_acceptable_candidate" and run.selection.repairable and run.repair_round < budget.max_repair_rounds and len(run.candidate_specs) < budget.max_candidates:
        run.repair_round += 1
        repair_spec = _build_repair_spec(run, run.repair_round)
        run.candidate_specs.append(repair_spec)
        run.transition(GroRunState.REPAIR_GROUP, reason=f"round {run.repair_round}: no candidate passed every gate; repairing the proven shortfall")
        run.transition(GroRunState.READY, reason=f"repair candidate {repair_spec.candidate_id} planned")
        run.transition(GroRunState.RUNNING, reason="executing repair candidate")
        emit("repair.started", round=run.repair_round, candidate_id=repair_spec.candidate_id, attempt=repair_spec.attempt)
        outcomes = coordinator.execute_group([repair_spec], adapter.run, started_at=now())
        run.outcomes.update(outcomes)
        for candidate_id, outcome in outcomes.items():
            verdict = _safe_verifier(verifier, run.contract, outcome)
            run.evaluations[candidate_id] = evaluate_candidate(
                run.contract,
                outcome,
                verdict,
                candidate_timeout_seconds=budget.candidate_timeout_seconds,
            )
        run.transition(GroRunState.VERIFYING, reason="repair candidate executed; re-verifying the group")
        rank_group(list(run.evaluations.values()))
        selection = select_candidate(list(run.evaluations.values()))
        run.selection = selection
        run.transition(GroRunState.RANKED, reason=f"re-ranked after repair round {run.repair_round}")
        run.transition(
            GroRunState.SELECTED if selection.outcome == "selected" else GroRunState.NO_ACCEPTABLE_CANDIDATE,
            reason=selection.reason,
        )
        emit("repair.completed", round=run.repair_round, outcome=selection.outcome, selected=selection.selected_candidate_id)


def _build_repair_spec(run: GroRun, round_number: int) -> CandidateSpec:
    """Build the failure-directed repair candidate from proven-shortfall evidence."""

    failed = [e for e in run.evaluations.values() if not e.eligible and _gate_status(e, GATE_EXECUTION) is GateStatus.PASS]
    failed.sort(key=lambda e: e.candidate_id)
    source = failed[0] if failed else None
    lines: list[str] = []
    if source is not None:
        for gate in source.gates:
            if gate.required and gate.status is not GateStatus.PASS:
                lines.append(f"- {gate.name} [{gate.status.value}]: {gate.detail}")
        outcome = run.outcomes.get(source.candidate_id)
        if outcome is not None and outcome.output_text.strip():
            lines.append(f"Previous attempt output (tail): …{outcome.output_text[-_REPAIR_CONTEXT_TAIL:]}")
        lines.append(f"Repair target candidate: {source.candidate_id}")
    else:
        lines.append("No completed prior attempt; start fresh and address the acceptance criteria directly.")
    return CandidateSpec(
        candidate_id=f"cand_r{round_number}",
        run_id=run.run_id,
        strategy=REPAIR_STRATEGY,
        objective=run.contract.goal,
        acceptance_criteria=run.contract.acceptance_criteria,
        attempt=round_number + 1,
        repair_context="\n".join(lines)[:4000],
    )


def _gate_status(evaluation: CandidateEvaluation, name: str) -> GateStatus | None:
    for gate in evaluation.gates:
        if gate.name == name:
            return gate.status
    return None


def _safe_verifier(verifier: Verifier, contract: TaskContract, outcome: CandidateOutcome) -> Mapping[str, Any] | None:
    try:
        return verifier(contract, outcome)
    except Exception as exc:  # noqa: BLE001 - a checker outage is UNKNOWN, never a pass
        logger.warning("GRO verifier raised for candidate %s: %s", outcome.candidate_id, f"{type(exc).__name__}: {exc}", exc_info=True)
        return None


def _persist(run: GroRun, store: Any | None) -> bool:
    if store is None:
        return False
    try:
        return bool(store.save(run))
    except Exception:  # noqa: BLE001 - save() already reports; belt and braces
        logger.warning("GRO run %s persist raised", run.run_id, exc_info=True)
        return False


def _finalize_report(run: GroRun, cfg: GroConfig, store: Any, verifier: Verifier, emit: Callable[..., None], events: list[dict[str, Any]]) -> GroReport:
    """Phase G: final verification, terminal state, persistence, report."""

    selection = run.selection
    report = GroReport(run=run, outcome="failed", events=events)
    report.not_started_count = sum(1 for outcome in run.outcomes.values() if outcome.status is CandidateStatus.NOT_STARTED)

    if selection is None:
        run.transition(GroRunState.FAILED, reason="no selection was produced")
        emit("run.failed", error="no selection was produced")
        report.persisted = _persist(run, store)
        report.error = "no selection was produced"
        return report

    if selection.outcome == "selected" and selection.selected_candidate_id is not None:
        candidate_id = selection.selected_candidate_id
        outcome = run.outcomes.get(candidate_id)

        # Phase G: final verification against the live state before reporting.
        if cfg.require_final_verification:
            fresh_verdict = _safe_verifier(verifier, run.contract, outcome) if outcome is not None else None
            fresh_evaluation = evaluate_candidate(run.contract, outcome, fresh_verdict, candidate_timeout_seconds=run.budget.candidate_timeout_seconds) if outcome is not None else None
            if fresh_evaluation is None or not fresh_evaluation.eligible:
                detail = "; ".join(gate.detail for gate in (fresh_evaluation.gates if fresh_evaluation else []) if gate.required and gate.status is not GateStatus.PASS) or "outcome missing"
                if fresh_evaluation is not None:
                    run.evaluations[candidate_id] = fresh_evaluation
                run.selection = SelectionResult(
                    outcome="no_acceptable_candidate",
                    reason=f"final re-verification failed for {candidate_id}: {detail}",
                    ranking=selection.ranking,
                    repairable=False,
                )
                run.transition(GroRunState.NO_ACCEPTABLE_CANDIDATE, reason="final re-verification failed; the provisional winner is demoted rather than reported")
                emit("selection.demoted", candidate_id=candidate_id, detail=detail)
                selection = run.selection
            elif candidate_id in run.evaluations:
                run.evaluations[candidate_id] = fresh_evaluation

    if selection.outcome == "selected" and selection.selected_candidate_id is not None:
        candidate_id = selection.selected_candidate_id
        outcome = run.outcomes[candidate_id]
        evaluation = run.evaluations.get(candidate_id)
        criteria_count = len(run.contract.acceptance_criteria)
        report.outcome = "selected"
        report.winner_candidate_id = candidate_id
        report.winner_text = outcome.output_text
        report.verified = bool(criteria_count) and evaluation is not None and evaluation.eligible
        report.verified_criteria_count = criteria_count if report.verified else 0
        run.transition(GroRunState.PROMOTED, reason=f"winner {candidate_id} passed every required gate ({'final re-verified' if cfg.require_final_verification else 'verified once; final re-verification disabled by config'})")
        emit("run.promoted", candidate_id=candidate_id, verified=report.verified)
    else:
        completed = [cid for cid, outcome in run.outcomes.items() if outcome.status is CandidateStatus.COMPLETED]
        if completed:
            partial_id = sorted(completed, key=lambda cid: selection.ranking.index(cid) if cid in selection.ranking else 99)[0]
            report.outcome = "partial_result"
            report.partial_candidate_id = partial_id
            report.partial_text = run.outcomes[partial_id].output_text
            run.transition(GroRunState.PARTIAL_RESULT, reason=f"no candidate passed every required gate; closest attempt {partial_id} returned as an explicit UNVERIFIED partial result")
            emit("run.partial", candidate_id=partial_id)
        else:
            report.outcome = "failed"
            report.error = selection.reason or "no candidate completed and none passed the gates"
            run.transition(GroRunState.FAILED, reason=report.error)
            emit("run.failed", error=report.error)

    report.persisted = _persist(run, store)
    if store is not None and getattr(store, "is_degraded", False):
        report.error = report.error or f"store degraded: {store.load_error}"
    emit("run.completed", outcome=report.outcome, state=run.state.value, verified=report.verified)
    return report


#: Terminal GRO state -> the report outcome that state was reached with.
_STATE_OUTCOMES: dict[GroRunState, str] = {
    GroRunState.PROMOTED: "selected",
    GroRunState.PARTIAL_RESULT: "partial_result",
    GroRunState.NO_ACCEPTABLE_CANDIDATE: "no_acceptable_candidate",
    GroRunState.NEEDS_HUMAN: "needs_human",
    GroRunState.FAILED: "failed",
}


def report_for_run(run: GroRun) -> GroReport:
    """Rebuild a report from a (persisted) run, for status reads.

    This derives every field from durable run state — it never re-runs the
    verifier and never promotes: a state that is not terminal reports
    ``in_progress`` with no winner, and ``verified`` is re-derived from the
    stored evaluation (criteria declared *and* the stored winner eligible),
    so a status read can only repeat what the run already proved.
    """

    outcome = _STATE_OUTCOMES.get(run.state, "in_progress")
    report = GroReport(run=run, outcome=outcome, persisted=True)
    report.not_started_count = sum(1 for outcome_ in run.outcomes.values() if outcome_.status is CandidateStatus.NOT_STARTED)
    selection = run.selection

    if run.state is GroRunState.PROMOTED and selection and selection.selected_candidate_id:
        candidate_id = selection.selected_candidate_id
        stored_outcome = run.outcomes.get(candidate_id)
        evaluation = run.evaluations.get(candidate_id)
        report.winner_candidate_id = candidate_id
        report.winner_text = stored_outcome.output_text if stored_outcome else ""
        criteria_count = len(run.contract.acceptance_criteria)
        report.verified = bool(criteria_count) and evaluation is not None and evaluation.eligible
        report.verified_criteria_count = criteria_count if report.verified else 0
    elif run.state is GroRunState.PARTIAL_RESULT:
        completed = [cid for cid, value in run.outcomes.items() if value.status is CandidateStatus.COMPLETED]
        if completed and selection:
            partial_id = sorted(completed, key=lambda cid: selection.ranking.index(cid) if cid in selection.ranking else 99)[0]
        else:
            partial_id = completed[0] if completed else None
        report.partial_candidate_id = partial_id
        if partial_id:
            report.partial_text = run.outcomes[partial_id].output_text
    elif run.state is GroRunState.FAILED:
        report.error = (selection.reason if selection else "") or "no detail recorded"
    return report
