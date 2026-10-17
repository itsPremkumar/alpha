"""Deterministic, model-free specification lint for swarm plans.

**The gap this closes.**  MAST's largest measured failure category (~42%) is
specification: the system was never told precisely enough what to do, or it
quietly stopped obeying.  Alpha's own recorded run completed 5/5 tasks with
``acceptance_status=not_required`` and a QA worker returned fabricated
repository paths -- the acceptance overlay executes only when
``task.acceptance_criteria`` is set, so a plan could exist, run and complete
without ever having been asked to prove anything.

This linter answers, before the first token is spent, whether the plan is
*checkable*: does every task that promises an artifact also declare how that
artifact will be accepted, do cited paths resolve against the workspace, and
are the directives distinct and bounded?  It is the swarm-plane analogue of
``alpha.planning.hyperplan`` -- a visible refusal, never a silent fix.

Three honesty rules are load-bearing:

- **Pure and deterministic.**  Same plan plus same workspace state produces
  byte-identical findings; no model call, no clock, no mutation of the plan.
- **A refusal is pre-registration.**  ``SwarmCoordinator.create_swarm`` raises
  on a ``refuse``-severity finding before the plan is registered, checkpointed
  or published -- a refused plan must never remain visible as "running".
- **An explicit waiver is recorded, not implied.**  A task that declares output
  artifacts without acceptance criteria is refused unless the caller passes
  ``acceptance_waiver=True``; the waiver then stays a visible ``warn`` finding
  and is recorded on the plan metrics, because "we chose not to check" is a
  decision an operator must be able to read back.

Findings carry ``{code, severity, task_id, evidence}``; severities are
``refuse`` and ``warn``.  The report basis string
(:data:`SPEC_LINT_BASIS`) versions the rule set so a persisted finding can
never be mistaken for output of a different linter.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

REFUSE = "refuse"
WARN = "warn"

#: Version of the rule set.  Persisted lint reports carry this so a reader can
#: tell which rules produced them.
SPEC_LINT_BASIS = "deterministic_spec_lint_v1"

#: Total finding bound: two findings per task can exist across several codes,
#: so a pathological plan is capped here rather than allowed to grow the
#: checkpoint.  Refusals are never dropped; warns are, with a disclosure.
MAX_FINDINGS = 128

#: Per-task cap on ``dangling_reference`` findings, so one directive full of
#: citations cannot crowd out the rest of the report.
_MAX_DANGLING_PER_TASK = 3

#: Directive phrasing that names a direction but no done-state.  Deliberately
#: matched against word boundaries: this is a *warn* vocabulary, and a
#: false positive costs one line of context, never an admission decision.
_VAGUE_DIRECTIVE_RE = re.compile(
    r"\b(?:"
    r"improve|improving|improvement|"
    r"handle|handling|"
    r"investigate|investigating|"
    r"polish|refine|refining|"
    r"enhance|enhancing|"
    r"clean\s+up|"
    r"review(?:\s+thoroughly)?|"
    r"as\s+needed|as\s+appropriate|if\s+necessary|"
    r"make\s+it\s+better|"
    r"look\s+into|"
    r"etc\."
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SpecFinding:
    """One lint finding.  ``task_id`` is ``None`` for plan-level findings."""

    code: str
    severity: str
    task_id: str | None
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "task_id": self.task_id,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class SpecLintReport:
    """Pure result of :func:`lint_plan`; ``refused`` is the admission decision."""

    findings: tuple[SpecFinding, ...] = ()
    acceptance_waiver: bool = False
    basis: str = SPEC_LINT_BASIS

    @property
    def refused(self) -> bool:
        return any(finding.severity == REFUSE for finding in self.findings)

    def count(self, severity: str) -> int:
        return sum(1 for finding in self.findings if finding.severity == severity)

    def refusal_message(self) -> str:
        """Actionable refusal text naming every code that caused it.

        Mirrors the cost-projection refusal: the prefix is stable, the codes
        are named, and the fix is stated rather than implied.
        """

        refusals = [finding for finding in self.findings if finding.severity == REFUSE]
        listed = "; ".join(f"[{finding.code}] task {finding.task_id!r}: {finding.evidence}" for finding in refusals[:5])
        remainder = len(refusals) - 5
        if remainder > 0:
            listed += f"; and {remainder} more refuse-severity finding(s)"
        return (
            f"swarm was refused before registration: specification lint found {len(refusals)} refuse-severity finding(s): {listed}. "
            "Fix the named task specifications (add acceptance criteria, name the artifact, or record acceptance_waiver=true) and retry."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "acceptance_waiver": self.acceptance_waiver,
            "refused": self.refused,
            "refuse_count": self.count(REFUSE),
            "warn_count": self.count(WARN),
            "findings": [finding.to_dict() for finding in self.findings],
        }


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _artifact_list(task: Any) -> list[str]:
    raw = getattr(task, "output_artifacts", None) or ()
    return [str(item) for item in raw if str(item)]


def _criteria_present(task: Any) -> bool:
    criteria = getattr(task, "acceptance_criteria", None) or ()
    return any(isinstance(item, Mapping) for item in criteria)


def _format_artifacts(artifacts: Sequence[str]) -> str:
    shown = artifacts[:3]
    suffix = f", +{len(artifacts) - 3} more" if len(artifacts) > 3 else ""
    return f"[{', '.join(shown)}{suffix}]"


def _vague_match(directive: str) -> str | None:
    match = _VAGUE_DIRECTIVE_RE.search(directive)
    return match.group(0) if match else None


def _dangling_references(directive: str) -> list[dict[str, Any]]:
    """Cited paths in a directive that resolve nowhere in the workspace.

    Reuses the S2 grounding extractor/resolver (``swarm/grounding.py``) so a
    pre-run directive check and a post-run claim check cannot disagree about
    what "this path does not resolve" means.  Only ``missing`` findings warn
    here: an out-of-range line number in a *directive* (which runs before the
    file might change) is noise, and an absolute/traversal path is already
    disclosed by the tool surface that carried it.
    """

    from alpha.swarm.grounding import ground_claims

    report = ground_claims(directive, max_claims=_MAX_DANGLING_PER_TASK + 2)
    dangling: list[dict[str, Any]] = []
    for claim in report.get("claims", ()):  # type: ignore[union-attr]
        if not isinstance(claim, Mapping) or claim.get("status") != "missing":
            continue
        dangling.append(
            {
                "kind": str(claim.get("kind") or "file"),
                "value": str(claim.get("value") or ""),
                "line": claim.get("line"),
            }
        )
        if len(dangling) >= _MAX_DANGLING_PER_TASK:
            break
    return dangling


def _write_scope_missing(task: Any) -> bool:
    """A *declared* mutating capability set with no declared write scope.

    Scoped to ``capability_source == "declared"`` on purpose: inferred or
    absent tags are a hint the composition layer resolves, and warning on
    every decomposed task would drown the report.  A declared mutating
    capability without any ``output_artifacts`` or ``worktree_path`` has no
    machine-readable statement of what it may touch -- advisory here because
    the write-scope *grant* lands with the worktree work (G5); the finding
    names the gap without inventing a field that does not exist yet.
    """

    if str(getattr(task, "capability_source", "none")) != "declared":
        return False
    tags = [str(tag) for tag in (getattr(task, "capability_tags", None) or []) if str(tag)]
    if not tags:
        return False
    if _artifact_list(task) or getattr(task, "worktree_path", None):
        return False
    try:
        from alpha.sandbox.worktree_strategy import task_signals_for

        signals = task_signals_for(task)
    except Exception:  # noqa: BLE001 - an advisory check never fails admission
        return False
    return not bool(getattr(signals, "read_only", False))


def lint_plan(plan: Any, *, acceptance_waiver: bool = False) -> SpecLintReport:
    """Lint a :class:`~alpha.swarm.models.SwarmPlan` without touching it.

    Deterministic by construction: tasks are visited in ``task_id`` order and
    every finding string is derived from task fields plus workspace existence
    checks only.
    """

    refusals: list[SpecFinding] = []
    warnings: list[SpecFinding] = []
    seen_directives: dict[str, str] = {}

    tasks = getattr(plan, "tasks", None) or {}
    for task in sorted(tasks.values(), key=lambda item: str(getattr(item, "task_id", ""))):
        task_id = str(getattr(task, "task_id", "")) or None
        directive = _as_text(getattr(task, "objective", ""))
        artifacts = _artifact_list(task)
        has_criteria = _criteria_present(task)

        if not directive:
            refusals.append(SpecFinding("empty_objective", REFUSE, task_id, "the task carries no directive; nothing can be executed or accepted from it"))
            continue

        if not has_criteria:
            if artifacts and not acceptance_waiver:
                refusals.append(
                    SpecFinding(
                        "acceptance_missing",
                        REFUSE,
                        task_id,
                        f"declares output artifact(s) {_format_artifacts(artifacts)} but no acceptance criteria; add criteria or pass acceptance_waiver=true",
                    )
                )
            elif artifacts and acceptance_waiver:
                warnings.append(
                    SpecFinding(
                        "acceptance_waived",
                        WARN,
                        task_id,
                        f"declares output artifact(s) {_format_artifacts(artifacts)} without acceptance criteria; an explicit acceptance_waiver is recorded on the plan",
                    )
                )
            else:
                warnings.append(
                    SpecFinding(
                        "acceptance_missing",
                        WARN,
                        task_id,
                        "no acceptance criteria: this task can only be executed, never accepted (acceptance_status will be 'not_required')",
                    )
                )

        vague = _vague_match(directive)
        if vague and not artifacts and not has_criteria:
            warnings.append(
                SpecFinding(
                    "vague_directive",
                    WARN,
                    task_id,
                    f"directive contains {vague!r} with no named artifact and no acceptance criteria; the done-state is undefined",
                )
            )

        for dangling in _dangling_references(directive):
            value = dangling["value"]
            line = dangling.get("line")
            cited = f"'{value}:{line}'" if line else f"'{value}'"
            warnings.append(
                SpecFinding(
                    "dangling_reference",
                    WARN,
                    task_id,
                    f"{dangling['kind']} {cited} does not resolve under the project or repository root (a planned file may be fine; a stale citation is not)",
                )
            )

        if _write_scope_missing(task):
            warnings.append(
                SpecFinding(
                    "no_declared_write_scope",
                    WARN,
                    task_id,
                    f"declared capability tags {sorted(str(tag) for tag in (getattr(task, 'capability_tags', None) or []))} are not all read-only, but the task declares no output_artifacts and no worktree_path",
                )
            )

        key = re.sub(r"\s+", " ", directive).strip().lower()
        if key:
            first = seen_directives.get(key)
            if first is not None:
                warnings.append(
                    SpecFinding(
                        "duplicate_directive",
                        WARN,
                        task_id,
                        f"directive duplicates task {first!r}; two tasks with one directive is step repetition, not parallelism",
                    )
                )
            else:
                seen_directives[key] = str(task_id)

    findings = refusals + warnings
    if len(findings) > MAX_FINDINGS:
        dropped = len(findings) - MAX_FINDINGS
        findings = findings[:MAX_FINDINGS] + [SpecFinding("findings_truncated", WARN, None, f"{dropped} warn-severity finding(s) dropped by the {MAX_FINDINGS}-finding report bound")]
    return SpecLintReport(findings=tuple(findings), acceptance_waiver=bool(acceptance_waiver))
