"""The improvement-run orchestrator (guide §7, §12.4).

One run: qualify the task, snapshot the baseline, execute it, form two bounded
proposals, validate, probe each candidate in its own workspace, contrast,
compose conservatively, execute and verify the successor, select the
deliverable, reflect into the experience library. Every stage transition is
persisted before the next stage starts; a crash resumes from the last completed
checkpoint and a BLOCKED run (missing collaborator) resumes by re-entering the
stage it parked at.

The collaborators are injected and **may be absent**, and absence is reported
rather than simulated: with no executor the run parks at ``BLOCKED`` saying so,
exactly like ``alpha.capability_mesh``'s executor-absent posture. The engine
never fabricates a probe outcome, a validator result, or a deliverable.

Concurrency: a run is guarded by a cross-process :class:`FileLock` for the
duration of its execution, so the same run cannot be driven by two workers.
Durability is single-process JSON/JSONL — atomic per write, never cross-process
exactly-once.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from typing import Any

from alpha.harness_improvement.budgets import BudgetCeilings, BudgetExceeded, BudgetState, StageCharge
from alpha.harness_improvement.components import UnknownComponentsError, build_bundle, resolve_components
from alpha.harness_improvement.compose import ComposerInput, DeliverableCandidate, compose_successor, select_deliverable
from alpha.harness_improvement.contrast import build_contrast
from alpha.harness_improvement.executor import (
    EditorAbsent,
    ExecutorAbsent,
    HarnessEditor,
    HarnessExecutor,
    ImprovementContext,
    TaskSpec,
    require_editor,
    require_executor,
)
from alpha.harness_improvement.experiences import ExperienceQuery, build_experience, save_experience, select_experiences
from alpha.harness_improvement.models import (
    CandidateScope,
    ComponentKind,
    HarnessBundle,
    HarnessProposal,
    ImprovementStage,
    ProbeOutcome,
    PromotionState,
    TaskContract,
    TokenUsage,
    ValidatorResult,
    ValidatorStatus,
    canonical_json,
    sha256_hex,
)
from alpha.harness_improvement.patches import PatchPolicy, validate_proposal
from alpha.harness_improvement.probes import (
    ProbeEnvironment,
    ProbeWorkspace,
    cleanup_probe_workspace,
    create_probe_workspace,
    environment_fingerprint,
    materialize_bundle,
)
from alpha.harness_improvement.promotion import PromotionPolicy
from alpha.harness_improvement.state import ImprovementRun, new_run, stage_key
from alpha.harness_improvement.store import HarnessImprovementStore, StoreCorrupt, StoreUnreadable, safe_id, utc_now
from alpha.harness_improvement.validators import run_deliverable_checks, validator_summary
from alpha.utils.file_lock import FileLock, FileLockTimeout


class ImprovementRunError(RuntimeError):
    """A run could not be started, resumed or driven, with the real reason."""


@dataclass(slots=True)
class HarnessImprovementSettings:
    """Operator configuration, mirroring the guide's §17 block.

    Built from a plain mapping by :meth:`from_mapping`, so the harness package
    never imports ``alpha.config``'s pydantic models (the Gateway converts).
    """

    enabled: bool = False
    mode: str = "sequential"
    max_steps: int = 1
    max_candidates_per_scope: int = 1
    max_concurrent_probes: int = 1
    max_wall_time_seconds: int = 300
    max_tool_calls_per_probe: int = 60
    max_model_turns_per_probe: int = 80
    max_diff_files: int = 8
    max_changed_bytes: int = 32768
    max_model_calls: int | None = 24
    max_cost_usd: float | None = None
    max_experience_entries_in_context: int = 5
    min_evidence_for_positive_lesson: int = 1
    isolate_filesystem: bool = True
    isolate_memory_writes: bool = True
    allow_external_side_effects: bool = False
    network_mode: str = "deny_by_default"
    allow_candidate_middleware_changes: bool = False
    allow_candidate_guardrail_policy_changes: bool = False
    automatic_global_promotion: bool = False
    require_regression_suite: bool = True
    require_human_approval_for_scripts: bool = True
    require_human_approval_for_middleware: bool = True
    require_human_approval_for_permissions: bool = True
    keep_last_known_good: bool = True
    model: str | None = None
    seed: int | None = None

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any] | None) -> HarnessImprovementSettings:
        """Build settings from a (possibly nested) config mapping.

        Unknown keys are refused, not ignored: a typo in an operator's config
        must not silently leave the conservative default in place while the
        operator believes a limit is set.
        """
        raw = dict(mapping or {})
        sandbox = dict(raw.pop("sandbox", {}) or {})
        promotion = dict(raw.pop("promotion", {}) or {})
        merged: dict[str, Any] = {**raw, **{k: v for k, v in sandbox.items() if k in cls._sandbox_keys()}, **{k: v for k, v in promotion.items() if k in cls._promotion_keys()}}
        unknown = sorted(set(raw) - cls._field_names()) + sorted(set(sandbox) - cls._sandbox_keys()) + sorted(set(promotion) - cls._promotion_keys())
        if unknown:
            raise ImprovementRunError(f"unknown harness_improvement settings: {', '.join(unknown)}")
        settings = cls()
        for key, value in merged.items():
            setattr(settings, key, value)
        return settings

    @classmethod
    def _field_names(cls) -> set[str]:
        return {f.name for f in fields(cls)}

    @classmethod
    def _sandbox_keys(cls) -> set[str]:
        return {"isolate_filesystem", "isolate_memory_writes", "allow_external_side_effects", "network_mode", "allow_candidate_middleware_changes", "allow_candidate_guardrail_policy_changes"}

    @classmethod
    def _promotion_keys(cls) -> set[str]:
        return {"automatic_global_promotion", "require_regression_suite", "require_human_approval_for_scripts", "require_human_approval_for_middleware", "require_human_approval_for_permissions", "keep_last_known_good"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "max_steps": self.max_steps,
            "max_candidates_per_scope": self.max_candidates_per_scope,
            "max_concurrent_probes": self.max_concurrent_probes,
            "max_wall_time_seconds": self.max_wall_time_seconds,
            "max_tool_calls_per_probe": self.max_tool_calls_per_probe,
            "max_model_turns_per_probe": self.max_model_turns_per_probe,
            "max_diff_files": self.max_diff_files,
            "max_changed_bytes": self.max_changed_bytes,
            "max_model_calls": self.max_model_calls,
            "max_cost_usd": self.max_cost_usd,
            "max_experience_entries_in_context": self.max_experience_entries_in_context,
            "min_evidence_for_positive_lesson": self.min_evidence_for_positive_lesson,
            "network_mode": self.network_mode,
            "automatic_global_promotion": self.automatic_global_promotion,
            "require_regression_suite": self.require_regression_suite,
        }

    def patch_policy(self) -> PatchPolicy:
        return PatchPolicy(
            max_diff_files=self.max_diff_files,
            max_changed_bytes=self.max_changed_bytes,
            allow_middleware_changes=self.allow_candidate_middleware_changes,
            allow_guardrail_policy_changes=self.allow_candidate_guardrail_policy_changes,
        )

    def promotion_policy(self) -> PromotionPolicy:
        return PromotionPolicy(
            automatic_global_promotion=self.automatic_global_promotion,
            require_regression_suite=self.require_regression_suite,
            require_human_approval_for_scripts=self.require_human_approval_for_scripts,
            require_human_approval_for_middleware=self.require_human_approval_for_middleware,
            require_human_approval_for_permissions=self.require_human_approval_for_permissions,
            keep_last_known_good=self.keep_last_known_good,
        )

    def ceilings(self) -> BudgetCeilings:
        return BudgetCeilings(
            max_model_calls=self.max_model_calls,
            max_cost_usd=self.max_cost_usd,
            max_wall_time_seconds=self.max_wall_time_seconds,
            max_tool_calls_per_probe=self.max_tool_calls_per_probe,
            max_model_turns_per_probe=self.max_model_turns_per_probe,
            max_parallel_probes=self.max_concurrent_probes,
        )

    def config_hash(self) -> str:
        return f"sha256:{sha256_hex(canonical_json({**self.to_dict(), 'patch_policy': self.patch_policy().to_dict(), 'promotion_policy': self.promotion_policy().to_dict()}))}"


#: A baseline provider supplies the harness surface when no bundle is active.
BaselineProvider = Callable[[], Mapping[str, "tuple[ComponentKind, str]"]]


class HarnessImprovementEngine:
    """Drives one improvement run at a time; the store is the single owner of state."""

    def __init__(
        self,
        *,
        store: HarnessImprovementStore | None = None,
        settings: HarnessImprovementSettings | None = None,
        executor: HarnessExecutor | None = None,
        editor: HarnessEditor | None = None,
        baseline_provider: BaselineProvider | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store or HarnessImprovementStore()
        self.settings = settings or HarnessImprovementSettings()
        self.executor = executor
        self.editor = editor
        self.baseline_provider = baseline_provider
        self._clock = clock

    # ------------------------------------------------------------------ API
    async def start(self, *, task: TaskContract, created_by: str = "operator", run_id: str | None = None, base_bundle_id: str | None = None) -> ImprovementRun:
        """Qualify the task, create the durable run record, and drive it.

        ``base_bundle_id`` overrides the active bundle as the run's baseline.
        This is the adaptive-recursion seam: run N+1 can start from run N's
        successor bundle instead of the active one, so improvement compounds.
        """
        if not self.settings.enabled:
            raise ImprovementRunError("harness_improvement is disabled; enable it in config.yaml before starting a run")
        if self.settings.mode != "sequential":
            raise ImprovementRunError(f"mode {self.settings.mode!r} is not implemented in the MVP; use 'sequential'")
        refusal = qualify_task(task)
        if refusal:
            raise ImprovementRunError(f"task is not eligible for harness improvement: {refusal}")
        if base_bundle_id is not None:
            base_bundle = await asyncio.to_thread(self.store.load_bundle, base_bundle_id)
            if base_bundle is None:
                raise ImprovementRunError(f"base bundle {base_bundle_id!r} is not stored; cannot start a recursive run from a missing baseline")
        else:
            base_bundle = self._resolve_or_bootstrap_baseline()
        if base_bundle is None:
            raise ImprovementRunError("no active harness bundle exists and no baseline_provider is wired; refusing to invent a baseline")
        chosen_run_id = run_id or f"run-{sha256_hex(f'{created_by}:{task.task_fingerprint}:{utc_now()}')[:16]}"
        run = new_run(run_id=chosen_run_id, task=task, base_bundle_id=base_bundle.bundle_id, config_hash=self.settings.config_hash(), created_by=created_by, mode=self.settings.mode)
        run.alpha_runtime_version = base_bundle.alpha_runtime_version
        run.tool_schema_version = base_bundle.tool_schema_version
        run.policy_version = base_bundle.policy_version
        self._persist(run)
        self._event(run, "run.created", reason=f"created by {created_by}")
        return await self._drive(run)

    async def resume(self, run_id: str) -> ImprovementRun:
        """Resume an unfinished or BLOCKED run from its last completed checkpoint."""
        run = self._load_run(run_id)
        if run is None:
            raise ImprovementRunError(f"run {run_id!r} does not exist")
        if run.stage in {ImprovementStage.COMPLETED, ImprovementStage.CANCELLED, ImprovementStage.FAILED, ImprovementStage.BUDGET_EXHAUSTED}:
            raise ImprovementRunError(f"run {run_id!r} is {run.stage} and cannot be resumed")
        return await self._drive(run)

    def cancel(self, run_id: str, *, reason: str = "cancelled by operator") -> ImprovementRun:
        """Request cancellation; applied at the next stage boundary."""
        run = self._load_run(run_id)
        if run is None:
            raise ImprovementRunError(f"run {run_id!r} does not exist")
        if run.stage in {ImprovementStage.COMPLETED, ImprovementStage.CANCELLED, ImprovementStage.FAILED, ImprovementStage.BUDGET_EXHAUSTED}:
            raise ImprovementRunError(f"run {run_id!r} is already {run.stage}")
        run.cancel_requested = True
        run.cancel_reason = reason
        self._persist(run)
        self._event(run, "run.cancel_requested", reason=reason)
        return run

    def status(self, run_id: str) -> dict[str, Any] | None:
        run = self._load_run(run_id)
        if run is None:
            return None
        events, unreadable = self.store.read_events(run_id)
        return {
            "run": run.to_dict(),
            "events": events,
            "unreadable_event_lines": unreadable,
            "resumable": run.resumable,
            "next_stage": str(run.next_incomplete_stage()),
            "durability": "single-process JSON/JSONL; never cross-process exactly-once",
        }

    # ------------------------------------------------------------- internals
    def _load_run(self, run_id: str) -> ImprovementRun | None:
        payload = self.store.load_run(run_id)
        if payload is None:
            return None
        return ImprovementRun.from_dict(payload)

    def _persist(self, run: ImprovementRun) -> None:
        run.updated_at = utc_now()
        self.store.save_run(run)

    def _event(self, run: ImprovementRun, event: str, *, reason: str = "", **fields: Any) -> None:
        payload = {"event": event, "run_id": run.run_id, "stage": str(run.stage), "at": utc_now(), "reason": reason}
        payload.update(fields)
        self.store.append_event(run.run_id, payload)

    def _transition(self, run: ImprovementRun, stage: ImprovementStage, *, reason: str = "") -> None:
        run.transition(stage, reason=reason)
        self._persist(run)
        self._event(run, "stage.changed", reason=reason, target_stage=str(stage))

    def _resolve_or_bootstrap_baseline(self) -> HarnessBundle | None:
        pointer = self.store.load_active()
        if pointer is not None:
            bundle = self.store.load_bundle(pointer.bundle_id)
            if bundle is None:
                raise StoreCorrupt(f"active pointer names bundle {pointer.bundle_id} which is not stored")
            return bundle
        if self.baseline_provider is None:
            return None
        components = self.baseline_provider()
        bundle, contents = build_bundle(components=components, parent_bundle_id=None, created_by="baseline_provider", promotion_state=PromotionState.ACTIVE)
        self.store.save_bundle(bundle, contents.items())
        return bundle

    def _bundle_contents(self, bundle: HarnessBundle) -> dict[str, str]:
        contents: dict[str, str] = {}
        for ref in bundle.components:
            content = self.store.get_blob(ref.sha256)
            if content is None:
                raise StoreCorrupt(f"bundle {bundle.bundle_id} references blob {ref.sha256} which is not stored")
            contents[ref.component_id] = content
        return contents

    def _run_workspace(self, run: ImprovementRun, candidate_id: str) -> ProbeWorkspace:
        return create_probe_workspace(self.store.run_workspace_root(run.run_id), run_id=run.run_id, candidate_id=candidate_id)

    def _environment(self, role: str) -> ProbeEnvironment:
        return ProbeEnvironment(
            role=role,
            model=self.settings.model,
            alpha_runtime_version="unknown",
            tool_schema_version="unknown",
            policy_version="unknown",
            network_mode=self.settings.network_mode,
            isolate_filesystem=self.settings.isolate_filesystem,
            isolate_memory_writes=self.settings.isolate_memory_writes,
            allow_external_side_effects=self.settings.allow_external_side_effects,
            seed=self.settings.seed,
        )

    # ------------------------------------------------------------------ drive
    async def _drive(self, run: ImprovementRun) -> ImprovementRun:
        lock = FileLock(self.store.run_path(run.run_id), timeout=0.0)
        try:
            lock.acquire()
        except FileLockTimeout as exc:
            raise ImprovementRunError(f"run {run.run_id!r} is already being driven by another worker: {exc}") from exc
        try:
            budget = BudgetState(ceilings=self.settings.ceilings(), run_id=run.run_id, started_at=self._clock())
            if run.stage is ImprovementStage.QUEUED:
                run.transition(ImprovementStage.SNAPSHOTTING, reason="run starts")
                self._persist(run)
                self._event(run, "stage.changed", reason="run starts", target_stage=str(run.stage))
            while not run.terminal:
                if run.cancel_requested:
                    self._transition(run, ImprovementStage.CANCELLED, reason=run.cancel_reason or "cancelled by operator")
                    self._event(run, "run.cancelled", reason=run.cancel_reason)
                    self._persist(run)
                    break
                stage = run.next_incomplete_stage()
                if stage is ImprovementStage.COMPLETED:
                    self._transition(run, ImprovementStage.COMPLETED, reason="every stage completed")
                    self._event(run, "run.completed")
                    self._persist(run)
                    break
                if run.stage is not stage:
                    self._transition(run, stage, reason="next incomplete stage")
                handler = self._handler_for(stage)
                if handler is None:
                    run.mark_complete(stage, "")
                    self._persist(run)
                    continue
                try:
                    budget.check_wall_clock(stage=str(stage))
                    await handler(run, budget)
                except ExecutorAbsent as exc:
                    self._transition(run, ImprovementStage.BLOCKED, reason=str(exc))
                    self._event(run, "run.blocked", reason=str(exc))
                    self._persist(run)
                    break
                except EditorAbsent as exc:
                    self._transition(run, ImprovementStage.BLOCKED, reason=str(exc))
                    self._event(run, "run.blocked", reason=str(exc))
                    self._persist(run)
                    break
                except BudgetExceeded as exc:
                    run.add_note(str(exc))
                    self._transition(run, ImprovementStage.BUDGET_EXHAUSTED, reason=str(exc))
                    self._event(run, "budget.warning", reason=str(exc), bound=exc.bound, spent=exc.spent, ceiling=exc.ceiling)
                    self._event(run, "run.budget_exhausted", reason=str(exc))
                    self._persist(run)
                    break
                except asyncio.CancelledError:
                    self._transition(run, ImprovementStage.CANCELLED, reason="cancelled while running")
                    self._persist(run)
                    raise
                except (StoreCorrupt, StoreUnreadable) as exc:
                    run.error = f"{type(exc).__name__}: {exc}"
                    self._transition(run, ImprovementStage.FAILED, reason=run.error)
                    self._event(run, "run.failed", reason=run.error)
                    self._persist(run)
                    break
                except Exception as exc:  # a stage failure is a durable, classified outcome
                    run.error = f"{type(exc).__name__}: {exc}"
                    self._transition(run, ImprovementStage.FAILED, reason=run.error)
                    self._event(run, "run.failed", reason=run.error)
                    self._persist(run)
                    break
                key = self._stage_key(run, stage)
                run.mark_complete(stage, key)
                run.budget_state = budget.to_dict()
                self._persist(run)
                if run.cancel_requested:
                    self._transition(run, ImprovementStage.CANCELLED, reason=run.cancel_reason or "cancelled by operator")
                    self._event(run, "run.cancelled", reason=run.cancel_reason)
                    self._persist(run)
                    break
            return run
        finally:
            lock.release()

    def _stage_key(self, run: ImprovementRun, stage: ImprovementStage) -> str:
        return stage_key(run_id=run.run_id, stage=stage, inputs_hash=self._inputs_hash(run, stage), config_hash=run.config_hash)

    def _inputs_hash(self, run: ImprovementRun, stage: ImprovementStage) -> str:
        inputs: dict[str, Any] = {"stage": str(stage), "task": run.task.task_fingerprint, "base_bundle_id": run.base_bundle_id}
        if stage is ImprovementStage.BASELINE_RUNNING:
            inputs["environment"] = environment_fingerprint(self._environment("baseline"))
        if stage is ImprovementStage.PROPOSING:
            inputs["baseline_run"] = [p.run_id for p in run.probe_outcomes if p.candidate_id == "baseline"]
        if stage is ImprovementStage.CANDIDATES_VALIDATING:
            inputs["proposals"] = sorted(p.proposal_id for p in run.proposals)
        if stage is ImprovementStage.PROBING:
            inputs["candidates"] = sorted(str(v.get("candidate_bundle_id")) for v in run.validations if v.get("accepted"))
        if stage is ImprovementStage.CONTRASTING:
            inputs["probes"] = sorted(p.run_id for p in run.probe_outcomes)
        if stage is ImprovementStage.COMPOSING:
            inputs["contrasts"] = sorted(c.candidate_id for c in run.contrasts)
        if stage is ImprovementStage.SUCCESSOR_RUNNING:
            inputs["successor_bundle_id"] = run.successor_bundle_id
        if stage is ImprovementStage.REFLECTING:
            inputs["experiences"] = sorted(run.experiences)
        return f"sha256:{sha256_hex(canonical_json(inputs))}"

    def _handler_for(self, stage: ImprovementStage) -> Callable[..., Any] | None:
        mapping = {
            ImprovementStage.SNAPSHOTTING: self._stage_snapshotting,
            ImprovementStage.BASELINE_RUNNING: self._stage_baseline_running,
            ImprovementStage.BASELINE_RECORDED: self._stage_baseline_recorded,
            ImprovementStage.PROPOSING: self._stage_proposing,
            ImprovementStage.CANDIDATES_VALIDATING: self._stage_candidates_validating,
            ImprovementStage.PROBING: self._stage_probing,
            ImprovementStage.CONTRASTING: self._stage_contrasting,
            ImprovementStage.COMPOSING: self._stage_composing,
            ImprovementStage.SUCCESSOR_RUNNING: self._stage_successor_running,
            ImprovementStage.VERIFYING: self._stage_verifying,
            ImprovementStage.DELIVERABLE_SELECTED: None,
            ImprovementStage.REFLECTING: self._stage_reflecting,
        }
        return mapping.get(stage)

    # -------------------------------------------------------------- stages
    async def _stage_snapshotting(self, run: ImprovementRun, budget: BudgetState) -> None:
        bundle = self.store.load_bundle(run.base_bundle_id)
        if bundle is None:
            bundle = self._resolve_or_bootstrap_baseline()
            if bundle is None:
                raise StoreCorrupt(f"run references base bundle {run.base_bundle_id!r} which is not stored")
            run.base_bundle_id = bundle.bundle_id
        contents = self._bundle_contents(bundle)
        run.add_note(f"baseline bundle {bundle.bundle_id} snapshotted with {len(bundle.components)} component(s)")
        run.budget_state = budget.to_dict()
        self._event(run, "bundle.snapshot", reason=bundle.bundle_id, components=[ref.component_id for ref in bundle.components])
        self._persist(run)
        if not contents and not bundle.components:
            run.add_note("baseline bundle carries no components; probes will run against an empty harness surface")
            self._persist(run)

    async def _stage_baseline_running(self, run: ImprovementRun, budget: BudgetState) -> None:
        budget.check_model_call(stage="baseline_running")
        executor = require_executor(self.executor)
        bundle = self._require_bundle(run.base_bundle_id)
        contents = self._bundle_contents(bundle)
        workspace = self._run_workspace(run, "baseline")
        materialize_bundle(workspace, bundle, contents)
        environment = self._environment("baseline")
        task = TaskSpec(contract=run.task, workspace=workspace)
        outcome = await executor.execute(run_id=run.run_id, role="baseline", task=task, bundle=bundle, contents=contents, environment=environment)
        outcome = self._normalize_outcome(outcome, run=run, candidate_id="baseline", bundle_id=run.base_bundle_id, environment=environment)
        run.probe_outcomes.append(outcome)
        budget.charge(StageCharge(stage="baseline_running", model_calls=1, usage=outcome.token_usage, elapsed_ms=outcome.elapsed_ms, tool_calls=outcome.tool_calls, model_turns=outcome.model_turns))
        self.store.append_ledger(budget.ledger_entry(StageCharge(stage="baseline_running", model_calls=1, usage=outcome.token_usage, elapsed_ms=outcome.elapsed_ms), model=self.settings.model))
        run.budget_state = budget.to_dict()
        self._persist(run)
        self._event(run, "baseline.completed", reason=outcome.error or "", probe_run_id=outcome.run_id, errored=outcome.errored, validators=validator_summary(outcome.validator_results))

    async def _stage_baseline_recorded(self, run: ImprovementRun, budget: BudgetState) -> None:
        baseline = self._baseline_outcome(run)
        if baseline is None:
            raise StoreCorrupt("baseline stage completed without a recorded baseline outcome")
        failing = [result.name for result in baseline.validator_results if result.status is ValidatorStatus.FAILED]
        not_run = [result.name for result in baseline.validator_results if result.status is ValidatorStatus.NOT_RUN]
        note = f"baseline recorded: {validator_summary(baseline.validator_results)}"
        run.add_note(note)
        self._persist(run)
        self._event(run, "baseline.recorded", reason=note, failing_validators=failing, not_run_validators=not_run, trace_artifact=baseline.trace_artifact)

    async def _stage_proposing(self, run: ImprovementRun, budget: BudgetState) -> None:
        editor = require_editor(self.editor)
        baseline = self._baseline_outcome(run)
        bundle = self._require_bundle(run.base_bundle_id)
        contents = self._bundle_contents(bundle)
        experiences = self._experience_summaries(run)
        context = ImprovementContext(
            run_id=run.run_id,
            task=run.task,
            base_bundle=bundle,
            base_bundle_contents=contents,
            baseline_outcome=baseline,
            experience_summaries=experiences,
            patch_policy=self.settings.patch_policy(),
            budgets=budget,
            config_summary={"mode": self.settings.mode, "min_evidence_for_positive_lesson": self.settings.min_evidence_for_positive_lesson},
        )
        proposals: list[HarnessProposal] = []
        failures: list[str] = []
        for scope in (CandidateScope.INVARIANT, CandidateScope.TASK_OR_DOMAIN):
            budget.check_model_call(stage=f"proposing_{scope}")
            try:
                proposal = await editor.propose(context=context, scope=scope)
            except EditorAbsent as exc:
                failures.append(f"{scope}: {exc}")
                continue
            except Exception as exc:  # a malformed proposal is recorded, not fatal
                failures.append(f"{scope}: {type(exc).__name__}: {exc}")
                self._event(run, "proposal.invalid", reason=str(exc), scope=str(scope))
                continue
            if proposal.scope is not scope:
                proposal = replace(proposal, scope=scope)
            run.proposals.append(proposal)
            proposals.append(proposal)
            budget.charge(StageCharge(stage=f"proposing_{scope}", model_calls=1, usage=TokenUsage(), elapsed_ms=0))
            self.store.append_ledger(budget.ledger_entry(StageCharge(stage=f"proposing_{scope}", model_calls=1, usage=TokenUsage()), model=self.settings.model))
            self._event(run, "proposal.created", reason=proposal.hypothesis.summary, scope=str(scope), proposal_id=proposal.proposal_id)
        if not proposals:
            raise EditorAbsent(f"no proposal could be formed for any scope: {'; '.join(failures) or 'editor returned none'}")
        run.budget_state = budget.to_dict()
        self._persist(run)

    async def _stage_candidates_validating(self, run: ImprovementRun, budget: BudgetState) -> None:
        bundle = self._require_bundle(run.base_bundle_id)
        contents = self._bundle_contents(bundle)
        for proposal in run.proposals:
            validation = validate_proposal(proposal, bundle, base_contents=contents, policy=self.settings.patch_policy())
            record = validation.to_dict()
            record["scope"] = str(proposal.scope)
            record["rejection_details"] = [r.to_dict() for r in validation.rejections]
            run.validations.append(record)
            if validation.accepted and validation.candidate_bundle is not None:
                # Provenance is stamped here, not inside the patch builder: the
                # candidate's source run is what the promotion router later reads
                # to reconstruct its evidence, and the builder is a pure
                # function that must not need run context. Provenance is not
                # part of the bundle digest, so the id gained above is kept.
                candidate = replace(validation.candidate_bundle, source_run_id=run.run_id)
                self.store.save_bundle(candidate, validation.contents.items())
                self._event(run, "candidate.validated", reason=proposal.proposal_id, candidate_bundle_id=candidate.bundle_id)
            else:
                self._event(run, "candidate.validation_failed", reason="; ".join(r.message for r in validation.rejections), proposal_id=proposal.proposal_id)
        self._persist(run)

    async def _stage_probing(self, run: ImprovementRun, budget: BudgetState) -> None:
        budget.check_model_call(stage="probing")
        executor = require_executor(self.executor)
        selected = self._accepted_candidates(run)
        for candidate_id, bundle, contents, scope in selected:
            workspace = self._run_workspace(run, safe_id(candidate_id))
            materialize_bundle(workspace, bundle, contents)
            environment = self._environment(f"probe_{scope}")
            self._event(run, "probe.started", reason=candidate_id, candidate_bundle_id=bundle.bundle_id)
            self._persist(run)
            task = TaskSpec(contract=run.task, workspace=workspace)
            outcome = await executor.execute(run_id=run.run_id, role=f"probe_{scope}", task=task, bundle=bundle, contents=contents, environment=environment)
            outcome = self._normalize_outcome(outcome, run=run, candidate_id=candidate_id, bundle_id=outcome.bundle_id, environment=environment)
            budget.check_probe_limits(stage=f"probe_{scope}", tool_calls=outcome.tool_calls, model_turns=outcome.model_turns)
            cleanup_probe_workspace(workspace)
            run.probe_outcomes.append(outcome)
            budget.charge(StageCharge(stage=f"probe_{scope}", model_calls=1, usage=outcome.token_usage, elapsed_ms=outcome.elapsed_ms, tool_calls=outcome.tool_calls, model_turns=outcome.model_turns))
            self.store.append_ledger(budget.ledger_entry(StageCharge(stage=f"probe_{scope}", model_calls=1, usage=outcome.token_usage, elapsed_ms=outcome.elapsed_ms), model=self.settings.model))
            self._event(run, "probe.completed", reason=outcome.error or "", candidate_id=candidate_id, probe_run_id=outcome.run_id, errored=outcome.errored)
        run.budget_state = budget.to_dict()
        self._persist(run)

    async def _stage_contrasting(self, run: ImprovementRun, budget: BudgetState) -> None:
        baseline = self._baseline_outcome(run)
        if baseline is None:
            raise StoreCorrupt("cannot contrast without a baseline probe outcome")
        targeted = self._targeted_validator(run)
        by_candidate = {outcome.candidate_id: outcome for outcome in run.probe_outcomes}
        for validation in run.validations:
            if not validation.get("accepted"):
                continue
            candidate_id = str(validation.get("proposal_id"))
            outcome = by_candidate.get(candidate_id)
            if outcome is None:
                continue
            proposal = next((p for p in run.proposals if p.proposal_id == candidate_id), None)
            contrast = build_contrast(
                baseline,
                outcome,
                proposal_scope=proposal.scope if proposal else CandidateScope.INVARIANT,
                targeted_validator=targeted,
                expected_candidate_bundle_id=validation.get("candidate_bundle_id"),
            )
            run.contrasts.append(contrast)
            self._event(run, "contrast.ready", reason=contrast.verdict, candidate_id=candidate_id, verdict=str(contrast.verdict), uncertainty=list(contrast.uncertainty))
        self._persist(run)

    async def _stage_composing(self, run: ImprovementRun, budget: BudgetState) -> None:
        bundle = self._require_bundle(run.base_bundle_id)
        contents = self._bundle_contents(bundle)
        inputs: list[ComposerInput] = []
        for validation in run.validations:
            if not validation.get("accepted"):
                continue
            proposal_id = str(validation.get("proposal_id"))
            proposal = next((p for p in run.proposals if p.proposal_id == proposal_id), None)
            candidate_bundle = self.store.load_bundle(str(validation.get("candidate_bundle_id"))) if validation.get("candidate_bundle_id") else None
            contrast = next((c for c in run.contrasts if c.candidate_id == proposal_id), None)
            if proposal is None or candidate_bundle is None or contrast is None:
                continue
            candidate_contents = self._candidate_contents(bundle, contents, proposal)
            inputs.append(ComposerInput(proposal=proposal, contrast=contrast, candidate_bundle=candidate_bundle, candidate_contents=candidate_contents))
        decision, successor, successor_contents = compose_successor(base_bundle=bundle, base_contents=contents, inputs=inputs)
        run.composition = decision
        # Marginal improvement: the run found an improvement only if the
        # composer did not retain the baseline.  This is the honest signal
        # for adaptive recursion — a run that retained the baseline added
        # no information worth chaining from.
        run.marginal_improvement = not decision.retained_baseline
        # Adaptive stop decision (Phase 7): record whether the run should
        # stop (no more recursion) based on complexity, evidence relevance,
        # and marginal improvement.  Advisory — never blocks the run.
        try:
            from alpha.harness_improvement.complexity import decide_stop
            from alpha.harness_improvement.experiences import ExperienceMetadata

            listing = self.store.list_experiences()
            entries = [ExperienceMetadata.from_dict(row) for row in listing.items]
            stop = decide_stop(
                task=run.task,
                marginal_improvement=run.marginal_improvement,
                entries=entries,
                alpha_runtime_version=run.alpha_runtime_version,
                tool_schema_version=run.tool_schema_version,
            )
            run.add_note(f"adaptive stop: should_stop={stop.should_stop}, reason={stop.reason}")
            self._persist(run)
        except Exception:
            pass  # Adaptive decision is advisory; never block the run.
        if not decision.retained_baseline:
            # Same stamping rule as an accepted candidate: the composed
            # successor records the run that produced it, so the promotion
            # router can reconstruct probe/contrast/deliverable evidence from
            # the run record instead of reading every gate as unmeasured.
            successor = replace(successor, source_run_id=run.run_id)
            run.successor_bundle_id = successor.bundle_id
            self.store.save_bundle(successor, successor_contents.items())
        self._event(run, "composition.ready", reason=decision.rationale, retained_baseline=decision.retained_baseline, adopted=len(decision.adopted), rejected=len(decision.rejected))
        self._persist(run)

    async def _stage_successor_running(self, run: ImprovementRun, budget: BudgetState) -> None:
        decision = run.composition
        if decision is None or decision.retained_baseline or run.successor_bundle_id is None:
            run.add_note("no successor run: the composer retained the baseline harness")
            self._persist(run)
            self._event(run, "successor.skipped", reason="baseline retained by the composer")
            return
        budget.check_model_call(stage="successor_running")
        executor = require_executor(self.executor)
        bundle = self._require_bundle(run.successor_bundle_id)
        contents = self._bundle_contents(bundle)
        workspace = self._run_workspace(run, "successor")
        materialize_bundle(workspace, bundle, contents)
        environment = self._environment("successor")
        task = TaskSpec(contract=run.task, workspace=workspace)
        outcome = await executor.execute(run_id=run.run_id, role="successor", task=task, bundle=bundle, contents=contents, environment=environment)
        outcome = self._normalize_outcome(outcome, run=run, candidate_id="successor", bundle_id=bundle.bundle_id, environment=environment)
        run.probe_outcomes.append(outcome)
        budget.charge(StageCharge(stage="successor_running", model_calls=1, usage=outcome.token_usage, elapsed_ms=outcome.elapsed_ms, tool_calls=outcome.tool_calls, model_turns=outcome.model_turns))
        self.store.append_ledger(budget.ledger_entry(StageCharge(stage="successor_running", model_calls=1, usage=outcome.token_usage, elapsed_ms=outcome.elapsed_ms), model=self.settings.model))
        run.budget_state = budget.to_dict()
        self._persist(run)
        self._event(run, "successor.completed", reason=outcome.error or "", probe_run_id=outcome.run_id, errored=outcome.errored)

    async def _stage_verifying(self, run: ImprovementRun, budget: BudgetState) -> None:
        baseline = self._baseline_outcome(run)
        if baseline is None:
            raise StoreCorrupt("cannot verify without a baseline probe outcome")
        baseline_workspace = self._run_workspace(run, "baseline")
        baseline = await self._verify_workspace(baseline, baseline_workspace, run.task)
        successor: ProbeOutcome | None = None
        successor_workspace: ProbeWorkspace | None = None
        for outcome in run.probe_outcomes:
            if outcome.candidate_id == "successor":
                successor = outcome
                successor_workspace = self._run_workspace(run, "successor")
        if successor is not None and successor_workspace is not None:
            successor = await self._verify_workspace(successor, successor_workspace, run.task)
        decision = run.composition
        successor_supported = bool(decision is not None and not decision.retained_baseline and successor is not None)
        adopted_scope: CandidateScope | None = None
        if decision and decision.adopted:
            first_candidate = decision.adopted[0].candidate_id
            adopted_proposal = next((p for p in run.proposals if p.proposal_id == first_candidate), None)
            adopted_scope = adopted_proposal.scope if adopted_proposal else None
        selected = select_deliverable(
            baseline=DeliverableCandidate(source="baseline", bundle_id=baseline.bundle_id, probe_run_id=baseline.run_id, validators=baseline.validator_results, deliverable_refs=baseline.deliverable_refs, errored=baseline.errored),
            successor=(
                DeliverableCandidate(source="successor", bundle_id=successor.bundle_id, probe_run_id=successor.run_id, validators=successor.validator_results, deliverable_refs=successor.deliverable_refs, errored=successor.errored)
                if successor is not None
                else None
            ),
            successor_supported=successor_supported,
            successor_scope=adopted_scope,
        )
        selected["baseline_validators"] = [r.to_dict() for r in baseline.validator_results]
        selected["successor_validators"] = [r.to_dict() for r in successor.validator_results] if successor else None
        selected["successor_supported"] = successor_supported
        run.deliverable = selected
        self._persist(run)
        self._event(run, "evaluation.completed", reason=selected["reason"], selected=selected["selected"], bundle_id=selected["bundle_id"])

    async def _stage_reflecting(self, run: ImprovementRun, budget: BudgetState) -> None:
        written: list[str] = []
        for contrast in run.contrasts:
            result = build_experience(
                run=run,
                contrast=contrast,
                decision=run.composition,
                min_evidence_refs=self.settings.min_evidence_for_positive_lesson,
                trigger_symptoms=(f"validator failure: {self._targeted_validator(run)}" if self._targeted_validator(run) else "",),
                not_applicable_to=("conversational tasks with no deliverable",),
            )
            if result.entry is None:
                run.add_note(f"experience not written for {contrast.candidate_id}: {result.reason}")
                continue
            save_experience(self.store, result.entry)
            written.append(result.entry.experience_id)
            self._event(run, "experience.created", reason=result.entry.lesson, experience_id=result.entry.experience_id, finding=result.entry.finding)
        run.experiences.extend(written)
        run.add_note("promotion remains operator-gated: use /api/harness/candidates/{bundle_id}/approve then /api/harness/active/{bundle_id}/activate")
        self._persist(run)
        self._event(run, "reflection.completed", reason=f"{len(written)} experience entries written")

    # -------------------------------------------------------------- helpers
    def _require_bundle(self, bundle_id: str) -> HarnessBundle:
        bundle = self.store.load_bundle(bundle_id)
        if bundle is None:
            raise StoreCorrupt(f"bundle {bundle_id!r} is not stored")
        return bundle

    def _baseline_outcome(self, run: ImprovementRun) -> ProbeOutcome | None:
        for outcome in run.probe_outcomes:
            if outcome.candidate_id == "baseline":
                return outcome
        return None

    def _targeted_validator(self, run: ImprovementRun) -> str | None:
        baseline = self._baseline_outcome(run)
        if baseline is None:
            return None
        for result in baseline.validator_results:
            if result.status is ValidatorStatus.FAILED:
                return result.name
        return None

    def _normalize_outcome(self, outcome: ProbeOutcome, *, run: ImprovementRun, candidate_id: str, bundle_id: str, environment: ProbeEnvironment) -> ProbeOutcome:
        """Fill engine-owned identity fields; never invent measurements."""
        return replace(
            outcome,
            run_id=outcome.run_id or f"{run.run_id}:{candidate_id}",
            bundle_id=bundle_id,
            candidate_id=candidate_id,
            task_fingerprint=run.task.task_fingerprint,
            environment=environment.to_dict(),
        )

    def _accepted_candidates(self, run: ImprovementRun) -> list[tuple[str, HarnessBundle, dict[str, str], CandidateScope]]:
        bundle = self._require_bundle(run.base_bundle_id)
        contents = self._bundle_contents(bundle)
        selected: list[tuple[str, HarnessBundle, dict[str, str], CandidateScope]] = []
        per_scope: dict[str, int] = {}
        for validation in run.validations:
            if not validation.get("accepted") or not validation.get("candidate_bundle_id"):
                continue
            proposal_id = str(validation.get("proposal_id"))
            proposal = next((p for p in run.proposals if p.proposal_id == proposal_id), None)
            if proposal is None:
                continue
            scope_key = str(proposal.scope)
            if per_scope.get(scope_key, 0) >= max(1, self.settings.max_candidates_per_scope):
                run.add_note(f"candidate {proposal_id} was validated but not probed: per-scope candidate ceiling reached")
                continue
            per_scope[scope_key] = per_scope.get(scope_key, 0) + 1
            candidate_bundle = self._require_bundle(str(validation["candidate_bundle_id"]))
            selected.append((proposal_id, candidate_bundle, self._candidate_contents(bundle, contents, proposal), proposal.scope))
        return selected

    def _candidate_contents(self, base_bundle: HarnessBundle, base_contents: dict[str, str], proposal: HarnessProposal) -> dict[str, str]:
        try:
            resolution = resolve_components(base_bundle, proposal.component_diffs)
        except UnknownComponentsError as exc:
            raise StoreCorrupt(f"accepted proposal {proposal.proposal_id} references an unknown component: {exc}") from exc
        removed = {diff.component_id for diff in proposal.component_diffs if str(diff.op) == "remove"}
        contents: dict[str, str] = {}
        for component_id, (_kind, content) in resolution.items():
            if component_id in removed:
                continue
            contents[component_id] = content if content is not None else base_contents.get(component_id, "")
        return contents

    def _experience_summaries(self, run: ImprovementRun) -> tuple[dict[str, Any], ...]:
        listing = self.store.list_experiences()
        from alpha.harness_improvement.experiences import ExperienceMetadata

        entries = [ExperienceMetadata.from_dict(row) for row in listing.items]
        query = ExperienceQuery(
            task_type=run.task.task_type or None,
            domain_tags=tuple(run.task.domain_tags),
            text=run.task.outcome,
            alpha_runtime_version=run.alpha_runtime_version,
            tool_schema_version=run.tool_schema_version,
            limit=self.settings.max_experience_entries_in_context,
        )
        rows = select_experiences(entries, query)
        if listing.unreadable:
            run.add_note(f"experience library had {len(listing.unreadable)} unreadable entr(y/ies); they were not retrieved")
        return tuple(rows)

    async def _verify_workspace(self, outcome: ProbeOutcome, workspace: ProbeWorkspace, task: TaskContract) -> ProbeOutcome:
        """Run the task's deterministic checks against the actual outputs.

        A successful tool call is not proof the artifact is usable (guide §7
        step 8), so the engine reopens the deliverable itself. Engine-measured
        results replace an executor's same-named check; executor-only checks are
        kept, labelled, because losing them would lose evidence.
        """
        measured = await asyncio.to_thread(run_deliverable_checks, task.checks, workspace.root)
        measured_by_name = {result.name: result for result in measured}
        merged: list[ValidatorResult] = [replace(result, kind=f"{result.kind}:executor") for result in outcome.validator_results if result.name not in measured_by_name]
        merged.extend(measured)
        refs = tuple(sorted(str(path.relative_to(workspace.root)).replace("\\", "/") for path in workspace.outputs_dir.rglob("*") if path.is_file()))
        return replace(outcome, validator_results=tuple(merged), deliverable_refs=refs or outcome.deliverable_refs)


def qualify_task(task: TaskContract) -> str | None:
    """Return a refusal reason when the task should not be improved (guide §7 step 0).

    Recurse only when there is an observable outcome and something to check.
    """
    if not task.outcome.strip():
        return "the task contract states no requested outcome"
    if not task.checks and not task.deliverables:
        return "the task has no deliverable and no success check, so any comparison would be unfalsifiable"
    if task.uncertainties and "no observable" in " ".join(task.uncertainties).lower():
        return "the task contract already declares no observable success criteria"
    return None
