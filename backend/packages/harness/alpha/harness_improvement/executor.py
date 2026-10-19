"""Executor/editor seams: the two collaborators that actually spend tokens.

The engine never calls a model itself. It drives two injected collaborators
behind protocols (guide §6.2), which is what keeps the recursive-improvement
logic swappable and — more importantly for this repository — testable with
scripted actors and **honest when nothing is wired**:

* :class:`HarnessExecutor` runs one task under one bundle in one workspace and
  returns a :class:`ProbeOutcome`. The default production posture has **no**
  executor wired, so a run parks at ``BLOCKED`` with the reason instead of
  fabricating a trace.
* :class:`HarnessEditor` turns a redacted :class:`ImprovementContext` into a
  bounded proposal. Its output is untrusted input; the patch validator is the
  gate it always passes through.

:class:`ScriptedExecutor` and :class:`ScriptedEditor` are the offline actors
used by tests and the synthetic benchmark. They are deliberately boring: a
scripted executor answers by bundle id + role, so an offline run exercises the
real state machine without a model call.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from alpha.harness_improvement.budgets import BudgetState
from alpha.harness_improvement.models import (
    CandidateScope,
    HarnessBundle,
    HarnessProposal,
    ProbeOutcome,
    TaskContract,
)
from alpha.harness_improvement.patches import PatchPolicy
from alpha.harness_improvement.probes import ProbeEnvironment, ProbeWorkspace
from alpha.harness_improvement.redaction import RedactionFinding, redact_mapping


class ExecutorAbsent(RuntimeError):
    """No executor is wired, so the run cannot produce a real trace.

    Raised — not caught into a synthetic outcome — because a fabricated probe
    would become evidence for a candidate, and evidence nobody measured is
    worse than no evidence at all.
    """


class EditorAbsent(RuntimeError):
    """No editor is wired, so no proposal can be formed."""


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """The task as the executor receives it.

    ``contract`` is the improvement-visible task contract; ``workspace`` is the
    probe's own tree; ``shared_inputs`` are read-only task inputs the operator
    supplied (never the active harness directory).
    """

    contract: TaskContract
    workspace: ProbeWorkspace
    shared_inputs: dict[str, str] = field(default_factory=dict)

    def to_dict(self, *, include_task: bool = False) -> dict[str, Any]:
        return {
            "contract": self.contract.to_dict(include_task=include_task),
            "workspace": str(self.workspace.root),
            "shared_inputs": sorted(self.shared_inputs),
        }


@dataclass(frozen=True, slots=True)
class ImprovementContext:
    """What an editor is allowed to see — redacted before serialization.

    The context carries *references* to evidence (trace artifact paths, event
    ids, hashes) rather than raw tool output, so a prompt-injected tool result
    cannot ride an instruction into a candidate diff. Task text is included
    only for the editor's own task, never in the cross-run experience surface.
    """

    run_id: str
    task: TaskContract
    base_bundle: HarnessBundle
    base_bundle_contents: Mapping[str, str]
    baseline_outcome: ProbeOutcome | None
    experience_summaries: tuple[dict[str, Any], ...]
    patch_policy: PatchPolicy
    budgets: BudgetState | None = None
    config_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_task: bool = True, include_content: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "task": self.task.to_dict(include_task=include_task),
            "base_bundle": {
                "bundle_id": self.base_bundle.bundle_id,
                "parent_bundle_id": self.base_bundle.parent_bundle_id,
                "components": [ref.to_dict() for ref in self.base_bundle.components],
                "compatibility": {
                    "alpha_runtime_version": self.base_bundle.alpha_runtime_version,
                    "tool_schema_version": self.base_bundle.tool_schema_version,
                    "policy_version": self.base_bundle.policy_version,
                },
            },
            "baseline_outcome": self.baseline_outcome.to_dict() if self.baseline_outcome else None,
            "experience_summaries": [dict(row) for row in self.experience_summaries],
            "patch_policy": self.patch_policy.to_dict(),
            "budget_status": self.budgets.to_dict() if self.budgets else None,
            "config_summary": self.config_summary,
        }
        if include_content:
            payload["base_bundle_contents"] = dict(self.base_bundle_contents)
        return payload

    def redacted_payload(self, *, include_task: bool = True, include_content: bool = False) -> tuple[Any, list[RedactionFinding]]:
        """The payload an untrusted editor receives: secrets scrubbed first."""
        return redact_mapping(self.to_dict(include_task=include_task, include_content=include_content))


@runtime_checkable
class HarnessExecutor(Protocol):
    """Runs one task under one bundle. Implementations own their own sandbox."""

    async def execute(
        self,
        *,
        run_id: str,
        role: str,
        task: TaskSpec,
        bundle: HarnessBundle,
        contents: Mapping[str, str],
        environment: ProbeEnvironment,
    ) -> ProbeOutcome: ...


@runtime_checkable
class HarnessEditor(Protocol):
    """Forms one bounded proposal for one scope from evidence."""

    async def propose(self, *, context: ImprovementContext, scope: CandidateScope) -> HarnessProposal: ...


def require_executor(executor: HarnessExecutor | None) -> HarnessExecutor:
    """Return *executor* or raise :class:`ExecutorAbsent` with the real reason."""
    if executor is None:
        raise ExecutorAbsent("no HarnessExecutor is wired into this process; the run cannot execute a real baseline or probe")
    return executor


def require_editor(editor: HarnessEditor | None) -> HarnessEditor:
    if editor is None:
        raise EditorAbsent("no HarnessEditor is wired into this process; no candidate proposal can be formed")
    return editor


# ---------------------------------------------------------------------------
# Offline actors (tests + synthetic benchmark). Never wired in production.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScriptedExecutor:
    """Deterministic executor for offline runs.

    ``outcomes`` maps ``(bundle_id, role)`` — or just ``bundle_id`` — to a
    factory receiving ``(workspace, bundle, environment)`` and returning a
    :class:`ProbeOutcome`. A missing entry is a refusal, not a default success.
    """

    outcomes: dict[str, Callable[[ProbeWorkspace, HarnessBundle, ProbeEnvironment], ProbeOutcome]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def execute(self, *, run_id: str, role: str, task: TaskSpec, bundle: HarnessBundle, contents: Mapping[str, str], environment: ProbeEnvironment) -> ProbeOutcome:
        self.calls.append({"run_id": run_id, "role": role, "bundle_id": bundle.bundle_id, "workspace": str(task.workspace.root), "environment": environment.to_dict()})
        factory = self.outcomes.get(f"{bundle.bundle_id}:{role}") or self.outcomes.get(bundle.bundle_id)
        if factory is None:
            raise ExecutorAbsent(f"scripted executor has no outcome for bundle {bundle.bundle_id} role {role}")
        return factory(task.workspace, bundle, environment)


@dataclass(slots=True)
class ScriptedEditor:
    """Deterministic editor: returns the proposal registered for a scope."""

    proposals: dict[CandidateScope, Callable[[ImprovementContext], HarnessProposal]] = field(default_factory=dict)
    contexts: list[dict[str, Any]] = field(default_factory=list)

    async def propose(self, *, context: ImprovementContext, scope: CandidateScope) -> HarnessProposal:
        redacted, findings = context.redacted_payload(include_task=True, include_content=True)
        self.contexts.append({"scope": str(scope), "payload": redacted, "redactions": [f.to_dict() for f in findings]})
        factory = self.proposals.get(scope)
        if factory is None:
            raise EditorAbsent(f"scripted editor has no proposal for scope {scope}")
        return factory(context)
