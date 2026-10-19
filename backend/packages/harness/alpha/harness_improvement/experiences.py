"""The evidence-gated experience library (guide §9).

A separate library from conversation/semantic/task memory, because its entries
do something those must not: they may *inform* a future proposal. That makes
pollution the primary risk, and four rules contain it:

* **No evidence, no lesson.** :func:`build_experience` returns ``None`` (with a
  reason) unless the contrast is positive or records an explicit regression,
  the probes were not confounded, and the configured evidence floor is met.
* **Scope travels with the lesson.** Every entry names its task types, domain
  tags, trigger symptoms and — explicitly — what it does *not* apply to. A
  retrieved entry is context, never an instruction.
* **Staleness is computed, not assumed.** An entry whose compatibility versions
  no longer match, whose review condition has passed, or that has been
  superseded is returned as STALE (or filtered out) rather than revived by a
  lexical match.
* **Negative findings are lessons too.** A candidate that regressed is recorded
  with its counter-evidence; the library is not a wall of wins.

The Markdown body is the guide's ``EXPERIENCE.md`` template; the metadata file
is what filtering and ranking actually read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.harness_improvement.models import (
    CandidateScope,
    CompositionDecision,
    ContrastReport,
    ContrastVerdict,
    canonical_json,
    sha256_hex,
)
from alpha.harness_improvement.state import ImprovementRun
from alpha.harness_improvement.store import HarnessImprovementStore, utc_now


class ExperienceStatus(StrEnum):
    """Lifecycle of one entry. PROPOSED entries are hypotheses, not lessons."""

    PROPOSED = "proposed"
    ACTIVE = "active"
    STALE = "stale"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


@dataclass(slots=True)
class ExperienceMetadata:
    """The machine-readable half of an entry."""

    experience_id: str
    title: str
    lesson: str
    finding: str  # "benefit" | "regression"
    scope: CandidateScope
    task_type: str
    domain_tags: tuple[str, ...]
    trigger_symptoms: tuple[str, ...]
    applicable_conditions: tuple[str, ...]
    not_applicable_to: tuple[str, ...]
    component_ids: tuple[str, ...]
    component_kinds: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    run_ids: tuple[str, ...]
    benefit: str
    counter_evidence: str
    status: ExperienceStatus
    created_at: str
    last_reviewed_at: str
    review_condition: str
    expires_at: str | None = None
    supersedes: tuple[str, ...] = ()
    superseded_by: str | None = None
    compatibility: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "experience_id": self.experience_id,
            "title": self.title,
            "lesson": self.lesson,
            "finding": self.finding,
            "scope": str(self.scope),
            "task_type": self.task_type,
            "domain_tags": list(self.domain_tags),
            "trigger_symptoms": list(self.trigger_symptoms),
            "applicable_conditions": list(self.applicable_conditions),
            "not_applicable_to": list(self.not_applicable_to),
            "component_ids": list(self.component_ids),
            "component_kinds": list(self.component_kinds),
            "evidence_refs": list(self.evidence_refs),
            "run_ids": list(self.run_ids),
            "benefit": self.benefit,
            "counter_evidence": self.counter_evidence,
            "status": str(self.status),
            "created_at": self.created_at,
            "last_reviewed_at": self.last_reviewed_at,
            "review_condition": self.review_condition,
            "expires_at": self.expires_at,
            "supersedes": list(self.supersedes),
            "superseded_by": self.superseded_by,
            "compatibility": self.compatibility,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExperienceMetadata:
        return cls(
            experience_id=str(data.get("experience_id", "")),
            title=str(data.get("title", "")),
            lesson=str(data.get("lesson", "")),
            finding=str(data.get("finding", "benefit")),
            scope=CandidateScope(data.get("scope", CandidateScope.TASK_OR_DOMAIN)),
            task_type=str(data.get("task_type", "general")),
            domain_tags=tuple(str(t) for t in data.get("domain_tags", [])),
            trigger_symptoms=tuple(str(s) for s in data.get("trigger_symptoms", [])),
            applicable_conditions=tuple(str(s) for s in data.get("applicable_conditions", [])),
            not_applicable_to=tuple(str(s) for s in data.get("not_applicable_to", [])),
            component_ids=tuple(str(c) for c in data.get("component_ids", [])),
            component_kinds=tuple(str(c) for c in data.get("component_kinds", [])),
            evidence_refs=tuple(str(e) for e in data.get("evidence_refs", [])),
            run_ids=tuple(str(r) for r in data.get("run_ids", [])),
            benefit=str(data.get("benefit", "")),
            counter_evidence=str(data.get("counter_evidence", "")),
            status=ExperienceStatus(data.get("status", ExperienceStatus.PROPOSED)),
            created_at=str(data.get("created_at", "")),
            last_reviewed_at=str(data.get("last_reviewed_at", "")),
            review_condition=str(data.get("review_condition", "")),
            expires_at=data.get("expires_at"),
            supersedes=tuple(str(s) for s in data.get("supersedes", [])),
            superseded_by=data.get("superseded_by"),
            compatibility=dict(data.get("compatibility", {})),
        )


@dataclass(frozen=True, slots=True)
class ExperienceBuildResult:
    """Either an entry, or ``None`` plus the reason it was not written."""

    entry: ExperienceMetadata | None
    reason: str = ""

    @property
    def written(self) -> bool:
        return self.entry is not None


def experience_id_for(*, run_id: str, candidate_id: str, finding: str = "benefit") -> str:
    """Deterministic id so a resumed reflection cannot mint a duplicate."""
    digest = sha256_hex(canonical_json({"run_id": run_id, "candidate_id": candidate_id, "finding": finding}))
    return f"exp-{digest[:16]}"


def build_experience(
    *,
    run: ImprovementRun,
    contrast: ContrastReport,
    decision: CompositionDecision | None,
    min_evidence_refs: int = 1,
    promoted: bool = False,
    expires_at: str | None = None,
    review_condition: str = "revalidate when the tool API/schema or runtime version changes",
    trigger_symptoms: Sequence[str] = (),
    not_applicable_to: Sequence[str] = (),
) -> ExperienceBuildResult:
    """Construct one entry, or refuse to — the refusal is a first-class result.

    A positive lesson requires a positive, unconfounded contrast whose targeted
    failure is not known to have recurred, with at least *min_evidence_refs*
    evidence references. A regression lesson requires a negative contrast with
    recorded regressions.
    """
    if contrast.verdict is ContrastVerdict.CONFOUNDED:
        return ExperienceBuildResult(entry=None, reason="contrast is confounded; a confounded probe cannot support a durable lesson")
    if contrast.verdict is ContrastVerdict.NEGATIVE:
        if not contrast.regressions:
            return ExperienceBuildResult(entry=None, reason="negative contrast recorded no specific regression to learn from")
        finding = "regression"
        benefit = "none observed"
        counter_evidence = "; ".join(contrast.regressions)
    elif contrast.verdict is ContrastVerdict.POSITIVE and contrast.targeted_failure_resolved is not False:
        finding = "benefit"
        benefit = "; ".join(contrast.improvements) or "targeted failure resolved"
        counter_evidence = "; ".join(contrast.regressions)
    else:
        return ExperienceBuildResult(entry=None, reason=f"contrast verdict {contrast.verdict} with targeted_failure_resolved={contrast.targeted_failure_resolved} is insufficient for a positive lesson")

    if len(contrast.evidence_refs) < max(1, min_evidence_refs):
        return ExperienceBuildResult(entry=None, reason=f"only {len(contrast.evidence_refs)} evidence refs; the configured floor is {min_evidence_refs}")

    component_ids: tuple[str, ...] = ()
    component_kinds: tuple[str, ...] = ()
    if decision is not None:
        adopted = tuple(a.diff for a in decision.adopted)
        component_ids = tuple(sorted({d.component_id for d in adopted}))
        component_kinds = tuple(sorted({str(d.kind) for d in adopted}))
    experience_id = experience_id_for(run_id=run.run_id, candidate_id=contrast.candidate_id, finding=finding)
    created_at = utc_now()
    status = ExperienceStatus.ACTIVE if (finding == "regression" or promoted) else ExperienceStatus.PROPOSED
    title = f"{'Regression' if finding == 'regression' else 'Improvement'} for {run.task.task_type or 'general'} tasks via {contrast.scope} scope"
    lesson = benefit if finding == "benefit" else f"avoid this change: {counter_evidence}"
    metadata = ExperienceMetadata(
        experience_id=experience_id,
        title=title,
        lesson=lesson,
        finding=finding,
        scope=contrast.scope,
        task_type=run.task.task_type or "general",
        domain_tags=tuple(run.task.domain_tags),
        trigger_symptoms=tuple(trigger_symptoms),
        applicable_conditions=(f"task type {run.task.task_type or 'general'}", f"runtime {run.alpha_runtime_version or 'unknown'}"),
        not_applicable_to=tuple(not_applicable_to),
        component_ids=component_ids,
        component_kinds=component_kinds,
        evidence_refs=tuple(contrast.evidence_refs),
        run_ids=(run.run_id,),
        benefit=benefit,
        counter_evidence=counter_evidence,
        status=status,
        created_at=created_at,
        last_reviewed_at=created_at,
        review_condition=review_condition,
        expires_at=expires_at,
        compatibility={
            "alpha_runtime_version": run.alpha_runtime_version,
            "tool_schema_version": run.tool_schema_version,
            "policy_version": run.policy_version,
        },
    )
    return ExperienceBuildResult(entry=metadata)


def render_experience_markdown(entry: ExperienceMetadata) -> str:
    """Render the guide's §9.2 ``EXPERIENCE.md`` template."""

    def _bullets(items: Iterable[str]) -> str:
        rows = [f"- {item}" for item in items]
        return "\n".join(rows) if rows else "- (none recorded)"

    return (
        f"# Experience: {entry.title}\n\n"
        "## Scope\n"
        f"- Domain/task types: {entry.task_type}\n"
        f"- Trigger symptoms:\n{_bullets(entry.trigger_symptoms)}\n"
        f"- Environment/tool/runtime assumptions:\n{_bullets(entry.applicable_conditions)}\n"
        f"- Explicitly not applicable to:\n{_bullets(entry.not_applicable_to)}\n\n"
        "## Evidence\n"
        f"- Baseline run / trace:\n{_bullets([r for r in entry.evidence_refs])}\n"
        f"- Candidate bundle(s): {', '.join(entry.component_ids) or '(none)'}\n"
        f"- Probe run(s): {', '.join(entry.run_ids)}\n"
        f"- Validator results: recorded in the run record\n"
        f"- Observed change and uncertainty: {entry.benefit}\n\n"
        "## Harness change\n"
        f"- Component(s): {', '.join(entry.component_ids) or '(none)'}\n"
        f"- Diff or bundle reference: see evidence refs\n"
        f"- Why this component is the cheapest effective intervention: scope={entry.scope}\n\n"
        "## Result\n"
        f"- Benefit observed: {entry.benefit}\n"
        f"- Regressions/counter-evidence: {entry.counter_evidence or 'none recorded'}\n"
        f"- Confidence/status: {entry.status}\n\n"
        "## Maintenance\n"
        f"- Created / last reviewed: {entry.created_at} / {entry.last_reviewed_at}\n"
        f"- Review or expiry condition: {entry.review_condition}\n"
        f"- Expires at: {entry.expires_at or 'no expiry; review condition applies'}\n"
        f"- Supersedes / superseded by: {', '.join(entry.supersedes) or '(none)'} / {entry.superseded_by or '(none)'}\n"
    )


def is_stale(entry: ExperienceMetadata, *, alpha_runtime_version: str = "", tool_schema_version: str = "", now: str | None = None) -> tuple[str, ...]:
    """Reasons this entry should not be retrieved as current guidance.

    An empty tuple means "no computed staleness" — not a guarantee of truth.
    """
    reasons: list[str] = []
    if entry.status is ExperienceStatus.SUPERSEDED or entry.superseded_by:
        reasons.append(f"superseded by {entry.superseded_by or 'a newer entry'}")
    if entry.status is ExperienceStatus.STALE:
        reasons.append("entry is marked stale")
    if entry.status is ExperienceStatus.RETRACTED:
        reasons.append("entry was retracted")
    if entry.expires_at and now and entry.expires_at <= now:
        reasons.append(f"expired at {entry.expires_at}")
    recorded_runtime = str(entry.compatibility.get("alpha_runtime_version") or "")
    if alpha_runtime_version and recorded_runtime and recorded_runtime != alpha_runtime_version:
        reasons.append(f"recorded against runtime {recorded_runtime}, current runtime is {alpha_runtime_version}")
    recorded_tools = str(entry.compatibility.get("tool_schema_version") or "")
    if tool_schema_version and recorded_tools and recorded_tools != tool_schema_version:
        reasons.append(f"recorded against tool schema {recorded_tools}, current schema is {tool_schema_version}")
    return tuple(reasons)


@dataclass(frozen=True, slots=True)
class ExperienceQuery:
    """Filter/ranking input. Every field is optional; empty means "no filter"."""

    task_type: str | None = None
    domain_tags: tuple[str, ...] = ()
    component_kinds: tuple[str, ...] = ()
    text: str = ""
    alpha_runtime_version: str = ""
    tool_schema_version: str = ""
    include_proposed: bool = True
    limit: int = 5


def select_experiences(
    entries: Sequence[ExperienceMetadata],
    query: ExperienceQuery,
    *,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Filter then rank entries; stale/superseded entries are excluded.

    Ranking is deliberately simple and explainable: exact task-type match,
    domain-tag overlap, component-kind overlap, lexical hits on title/lesson,
    then recency. The score is returned with its component parts so a caller
    can see why something was retrieved.
    """
    rows: list[dict[str, Any]] = []
    for entry in entries:
        if entry.status is ExperienceStatus.RETRACTED:
            continue
        if entry.finding == "benefit" and entry.status is ExperienceStatus.PROPOSED and not query.include_proposed:
            continue
        staleness = is_stale(entry, alpha_runtime_version=query.alpha_runtime_version, tool_schema_version=query.tool_schema_version, now=now)
        if staleness:
            continue
        if query.task_type and entry.task_type and entry.task_type != query.task_type:
            continue
        score = 0.0
        reasons: list[str] = []
        if query.task_type and entry.task_type == query.task_type:
            score += 4.0
            reasons.append("task type match")
        overlap = sorted(set(query.domain_tags) & set(entry.domain_tags))
        if overlap:
            score += 2.0 * len(overlap)
            reasons.append(f"domain tags: {', '.join(overlap)}")
        kinds = sorted(set(query.component_kinds) & set(entry.component_kinds))
        if kinds:
            score += 1.0 * len(kinds)
            reasons.append(f"component kinds: {', '.join(kinds)}")
        if query.text:
            needle = query.text.lower()
            hits = [token for token in needle.split() if token and (token in entry.title.lower() or token in entry.lesson.lower())]
            if hits:
                score += 1.5 * len(hits)
                reasons.append(f"lexical: {', '.join(sorted(set(hits)))}")
        if entry.status is ExperienceStatus.ACTIVE:
            score += 0.5
            reasons.append("active status")
        rows.append(
            {
                "experience_id": entry.experience_id,
                "title": entry.title,
                "lesson": entry.lesson,
                "finding": entry.finding,
                "scope": str(entry.scope),
                "status": str(entry.status),
                "score": round(score, 3),
                "reasons": reasons,
                "component_ids": list(entry.component_ids),
                "evidence_refs": list(entry.evidence_refs),
                "counter_evidence": entry.counter_evidence,
                "not_applicable_to": list(entry.not_applicable_to),
                "created_at": entry.created_at,
            }
        )
    rows.sort(key=lambda row: (-float(row["score"]), str(row["experience_id"])))
    return rows[: max(0, query.limit)]


def save_experience(store: HarnessImprovementStore, entry: ExperienceMetadata) -> None:
    """Persist the metadata and its Markdown body together."""
    store.save_experience(entry.experience_id, entry.to_dict(), render_experience_markdown(entry))


def load_experience(store: HarnessImprovementStore, experience_id: str) -> ExperienceMetadata | None:
    loaded = store.load_experience(experience_id)
    if loaded is None:
        return None
    metadata, _markdown = loaded
    return ExperienceMetadata.from_dict(metadata)


def supersede_experience(store: HarnessImprovementStore, *, old_id: str, new_id: str) -> tuple[bool, str]:
    """Mark *old_id* superseded by *new_id*, keeping both entries readable.

    Returns ``(changed, detail)`` — a refusal to supersede a missing entry is
    reported, never turned into a mutation of a record that does not parse.
    """
    old = load_experience(store, old_id)
    if old is None:
        return False, f"experience {old_id!r} does not exist"
    new = load_experience(store, new_id)
    if new is None:
        return False, f"experience {new_id!r} does not exist"
    old.superseded_by = new_id
    old.status = ExperienceStatus.SUPERSEDED
    old.last_reviewed_at = utc_now()
    new.supersedes = tuple(sorted({*new.supersedes, old_id}))
    save_experience(store, old)
    save_experience(store, new)
    return True, f"{old_id} superseded by {new_id}"
