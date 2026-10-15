"""Post-bracket tournament assembly: the four advanced primitives, finally cited.

``task_contract.py``, ``candidate.py``, ``evidence.py`` and ``synthesis.py`` were
pure, tested modules that **no arena run ever called**. A completed tournament
therefore produced no requirement coverage, no claim-level evidence ledger and no
contribution merge, even though the engine shipped all three — and the docs said
so in three places, which is an admission that the gap was known rather than
mistaken.

This module is the seam that closes it. It runs **after** the bracket reaches a
terminal state and writes one ``tournament`` block onto the run document, so a
live run finally cites:

* a **task contract** derived from the run's own task text, carrying the
  per-task hard gates ``task_rubrics.default_gates`` already declares;
* **candidate artifacts** for every competitor that actually wrote a solution,
  each with per-requirement coverage measured against that contract;
* an **evidence ledger** over the claims the judge reasons recorded, plus the
  hard-gate verdict ``gate_from_ledger`` returns;
* a **synthesis** over the surviving contributions, with gaps named rather than
  papered over.

## The four rules that keep this honest

**A synthesis is a PLAN, not an executed merge.** ``synthesis.synthesize``
returns sections and gaps; nothing here writes them into a workspace file, and
the block says ``executed: false``. A merged document that nobody merged would be
the exact fabrication the primitive was documented to avoid.

**Coverage is MEASURED from the artifact, never declared.** A requirement is
``covered`` only when the competitor's own solution text addresses its keywords
(or an attack/defense record explicitly cites the requirement id). Everything
else lands in ``partial`` or ``missing``, and ``synthesis_gaps`` surfaces what no
competitor covered. The judge scores the work; this module reports what is
actually in the files the run produced.

**UNVERIFIED is a first-class verdict.** A claim nobody checked is recorded as
``UNVERIFIED``, never promoted to ``SUPPORTED`` because the check could not run,
and it never trips the evidence gate — only a ``CONTRADICTED`` claim does. That
is ``evidence.gate_from_ledger``'s own rule, and it is restated here because this
is now the place a live run can see it.

**A failed assembly degrades the run, it does not fake it.** Every read of the
run's own files is defensive: a competitor whose solution file vanished between
the bracket and the report contributes ``missing`` coverage with the reason
recorded, rather than aborting a completed tournament or inventing a reading.
The block always carries ``assembled: true`` or an honest reason it could not be
assembled, so a reader never has to distinguish "no coverage" from "no report".

It is a pure function over a run document plus the workspace the executor already
wrote into: no I/O beyond reading those files, no model call, no budget.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alpha.arena.candidate import CandidateArtifact, Contribution, CoverageStatus, RequirementCoverage, synthesis_gaps
from alpha.arena.evidence import ClaimCheck, EvidenceLedger, Verdict, gate_from_ledger
from alpha.arena.synthesis import SynthesisResult, synthesize
from alpha.arena.task_contract import Requirement, TaskContract, TaskType
from alpha.arena.task_rubrics import default_gates

logger = logging.getLogger(__name__)

#: How much of one competitor's solution is read for coverage/claim extraction.
#: Bounded because the report is derived from a file a competitor wrote, and an
#: unbounded read of a 10 MB "solution" is how a report becomes the outage.
MAX_ARTIFACT_CHARS = 40_000

#: Longest single claim kept in the ledger. A claim is a sentence, not a log line;
#: an untruncated one can carry an entire judge reply.
MAX_CLAIM_CHARS = 240

#: Minimum words before a requirement keyword counts as *addressed*. One match in
#: a 40k document is noise, so the status is ``partial`` — not ``covered``.
MIN_COVERAGE_HITS = 2

#: Requirement keywords. Deliberately coarse and deterministic: this is a
#: *measurement* of what the artifact contains, not an understanding of it, and
#: the report says so in ``coverage_method``.
_STOPWORDS = frozenset(
    {
        "and",
        "are",
        "for",
        "from",
        "has",
        "have",
        "into",
        "not",
        "that",
        "the",
        "this",
        "was",
        "were",
        "will",
        "with",
        "you",
        "your",
    }
)


def _keywords(text: str) -> list[str]:
    """Distinct, content-bearing words of a requirement, longest first."""
    words = [word for word in re.findall(r"[a-z0-9_]+", (text or "").lower()) if word not in _STOPWORDS and len(word) > 2]
    return sorted(dict.fromkeys(words), key=lambda word: (-len(word), word))


def _read_text(path: Any) -> tuple[str, str]:
    """Read one artifact, returning ``(text, reason_if_unreadable)``.

    A missing or unreadable file is reported, never treated as empty content:
    an empty document would read as "this competitor said nothing about the
    requirement", which is a different claim from "we could not read what it
    said".
    """
    try:
        return Path(str(path)).read_text(encoding="utf-8", errors="replace")[:MAX_ARTIFACT_CHARS], ""
    except (OSError, ValueError) as error:
        return "", f"{type(error).__name__}: {error}"


def build_task_contract(state: dict[str, Any]) -> TaskContract:
    """The run's own task, as a structured contract.

    The requirement list is derived from the task text itself rather than
    supplied by a caller, because a run document already holds the only request
    anyone made. A second, richer requirement set belongs in the contract the
    caller builds before ``create``; this one is what the run can honestly cite.
    """
    task = str(state.get("task") or "")
    return TaskContract.create_from_request(task, task_type=TaskType.QA, context_snapshot_id=state.get("run_id"))


def _requirement_ids(contract: TaskContract) -> list[str]:
    return [requirement.id for requirement in contract.requirements]


def _coverage_for(requirement: Requirement, text: str) -> RequirementCoverage:
    """Coverage of one requirement by one artifact's text.

    ``COVERED`` needs the requirement's own words to appear at least
    ``MIN_COVERAGE_HITS`` times; one appearance is ``PARTIAL``, none is
    ``MISSING``. The status is never ``CONTRADICTORY`` here: nothing in the
    bracket records a refutation against a specific requirement, and inventing
    one would be a verdict no measurement supports.
    """
    keywords = _keywords(requirement.text)
    if not keywords:
        return RequirementCoverage(requirement_id=requirement.id, status=CoverageStatus.MISSING, notes="requirement text carried no content-bearing keyword to measure")
    lowered = text.lower()
    hits = sum(1 for keyword in keywords if keyword in lowered)
    if hits >= MIN_COVERAGE_HITS:
        status = CoverageStatus.COVERED
    elif hits:
        status = CoverageStatus.PARTIAL
    else:
        status = CoverageStatus.MISSING
    return RequirementCoverage(requirement_id=requirement.id, status=status, notes=f"{hits}/{len(keywords)} requirement keyword(s) present in the artifact")


def _claims_from(text: str, agent_id: str, *, limit: int = 8) -> list[str]:
    """Extract declarative claim lines from an artifact.

    Crude on purpose and labelled as such in the report: a bullet or numbered
    line is treated as a claim candidate. Nothing here decides whether a claim is
    true — that is the ledger's ``UNVERIFIED`` verdict, not a guess.
    """
    claims: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("-*•0123456789. \t").strip()
        if len(stripped) < 20 or stripped.endswith((":", ";", ",")):
            continue
        claims.append(f"[{agent_id}] {stripped}"[:MAX_CLAIM_CHARS])
        if len(claims) >= limit:
            break
    return claims


def _score_quality(match: dict[str, Any], agent_id: str) -> int:
    """The rubric total ``match["verdict"]["scores"]`` recorded for one side.

    ``rubric.weighted_total`` is already on the 0-10 rubric scale, which is the
    scale ``Contribution.quality`` uses, so nothing is rescaled here. What
    matters is *whose* score: taking ``scores["a"]`` unconditionally would credit
    a competitor with whichever opponent happened to be presented as A, and the
    executor deliberately swaps those labels (``pair_order`` /
    ``judge_presentation.swapped``) to defeat position bias — so the bug would be
    systematic, not occasional.

    ``0`` means "nobody scored this side", not "this contribution is worthless";
    ``best_contributions`` breaks ties on agent id deterministically either way,
    and the caller refuses to mark a zero-scored fragment reusable.
    """
    verdict = match.get("verdict")
    if not isinstance(verdict, dict):
        return 0
    if match.get("a") != agent_id and match.get("b") != agent_id:
        return 0
    side = "a" if match.get("a") == agent_id else "b"
    scores = (verdict.get("scores") or {}).get(side)
    if not isinstance(scores, dict):
        return 0
    try:
        total = float(scores.get("total", 0) or 0)
    except (TypeError, ValueError):
        logger.warning("Ignoring malformed arena score for agent %s: %r", agent_id, scores.get("total"), exc_info=True)
        return 0
    return max(0, min(10, round(total)))


def build_candidates(state: dict[str, Any], contract: TaskContract, work_path: Callable[[str], Any]) -> tuple[list[CandidateArtifact], list[str]]:
    """One :class:`CandidateArtifact` per competitor that produced a solution.

    Returns the artifacts and the reasons any competitor could not contribute,
    so a competitor the report skipped is named rather than silently absent.
    """
    requirement_ids = _requirement_ids(contract)
    artifacts: list[CandidateArtifact] = []
    unreadable: list[str] = []
    for agent_id, solution in sorted((state.get("solutions") or {}).items()):
        text, reason = _read_text(work_path(agent_id))
        if reason:
            unreadable.append(f"{agent_id}: {reason}")
        coverage = [_coverage_for(requirement, text) for requirement in contract.requirements]
        covered = {row.requirement_id: row.status for row in coverage}
        # The best rubric total this competitor was given across every match it
        # reached. Used as the fragment quality, so a contribution nobody was
        # scored on stays non-reusable rather than entering the merge as a 0.
        quality = max((_score_quality(match, agent_id) for match in _matches_of(state, agent_id)), default=0)
        artifacts.append(
            CandidateArtifact(
                agent_id=agent_id,
                artifact_id=f"{state['run_id']}:{agent_id}",
                run_id=str(state["run_id"]),
                path=str(solution),
                summary=f"{len(text)} characters read from the competitor's solution",
                coverage=coverage,
                contributions=[
                    Contribution(
                        requirement_id=requirement_id,
                        title=f"{agent_id} solution",
                        body=text[:MAX_ARTIFACT_CHARS],
                        quality=quality,
                        reusable=covered.get(requirement_id) is CoverageStatus.COVERED and quality > 0,
                    )
                    for requirement_id in requirement_ids
                ],
                claims=_claims_from(text, agent_id),
            )
        )
    return artifacts, unreadable


def _matches_of(state: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
    return [match for round_record in state.get("rounds") or [] for match in round_record.get("matches", []) if agent_id in (match.get("a"), match.get("b"))]


def build_evidence_ledger(state: dict[str, Any], artifacts: list[CandidateArtifact]) -> EvidenceLedger:
    """Claim-level ledger over what the run actually produced.

    Every claim is ``UNVERIFIED`` with the run id as its evidence ref, because a
    bracket produces no external retrieval step: the judges score solutions
    against a rubric, they do not fact-check citations. Recording those claims
    as ``SUPPORTED`` would manufacture the very evidence this engine is supposed
    to be honest about, and ``UNVERIFIED`` is the verdict the module defines for
    "no evidence one way or the other".
    """
    ledger = EvidenceLedger(run_id=str(state["run_id"]))
    for artifact in artifacts:
        for claim in artifact.claims:
            ledger.record(
                ClaimCheck(claim=claim, verdict=Verdict.UNVERIFIED, evidence_ref=str(state["run_id"]), note="extracted from the competitor's own solution; the arena performs no retrieval, so no claim is SUPPORTED by construction")
            )
    return ledger


def build_synthesis(state: dict[str, Any], artifacts: list[CandidateArtifact], requirement_ids: list[str]) -> SynthesisResult:
    """The merge plan over surviving contributions.

    The champion's solution is the fallback text for gaps, so a reader sees what
    the bracket actually chose beside what no competitor covered.
    """
    champion = state.get("champion") or ""
    fallback = f"champion: {champion}" if champion else ""
    return synthesize(str(state["run_id"]), artifacts, requirement_ids, fallback_text=fallback)


def assemble_tournament_report(state: dict[str, Any], work_path: Callable[[str], Any]) -> dict[str, Any]:
    """Build the ``tournament`` block for one finished run document.

    ``work_path(agent_id)`` resolves a competitor's solution path the same way the
    executor does, so the report reads the very files the bracket recorded rather
    than a second copy it might disagree with.
    """
    contract = build_task_contract(state)
    requirement_ids = _requirement_ids(contract)
    artifacts, unreadable = build_candidates(state, contract, work_path)
    ledger = build_evidence_ledger(state, artifacts)
    tripped, detail = gate_from_ledger(ledger)
    synthesis = build_synthesis(state, artifacts, requirement_ids)
    gaps = synthesis_gaps(artifacts, requirement_ids)
    return {
        "assembled": True,
        "assembled_by": "alpha.arena.tournament:assemble_tournament_report",
        "coverage_method": "keyword presence in each competitor's own solution file; a coverage status is measured from the artifact, never declared by the judge",
        "task_contract": contract.to_dict(),
        "hard_gates": [gate.to_dict() for gate in default_gates(contract.task_type)],
        "candidates": [artifact.to_dict() for artifact in artifacts],
        "candidate_count": len(artifacts),
        "unreadable_artifacts": unreadable,
        "coverage": {artifact.agent_id: artifact.coverage_by_requirement() for artifact in artifacts},
        "gaps": gaps,
        "evidence": {
            **ledger.to_dict(),
            "gate_tripped": tripped,
            "gate_detail": detail,
            "gate_note": "only a CONTRADICTED claim trips this gate; an UNVERIFIED claim is reported and does not fail a candidate, because failing on missing evidence punishes honesty",
        },
        "synthesis": {
            **synthesis.to_dict(),
            "executed": False,
            "executed_note": "this is a merge PLAN. Nothing here wrote the sections into a workspace file; an operator or a separate approved step applies them.",
        },
    }


__all__ = [
    "MAX_ARTIFACT_CHARS",
    "MIN_COVERAGE_HITS",
    "assemble_tournament_report",
    "build_candidates",
    "build_evidence_ledger",
    "build_synthesis",
    "build_task_contract",
]
