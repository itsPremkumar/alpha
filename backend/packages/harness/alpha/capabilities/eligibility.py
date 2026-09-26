"""Capability-tag vocabulary and task-eligibility computation.

This is the ONE place that answers "can this agent do this task?". It exists
because the same question was being asked three different ways:

* ``capability_tags`` on a :class:`alpha.swarm.models.SwarmTaskNode` was carried
  through planning and consulted for **leader election only** — never to decide
  which worker should get the task;
* :func:`alpha.bots.work_discovery.match_bot_for_task` scored bots with a fuzzy
  department + keyword heuristic, so a *mismatched* agent could still win;
* :func:`alpha.swarm.cnp_auction.SwarmWorkerAgent.calculate_bid` already
  implemented a strict rule (an agent that shares **no** tag does not bid at
  all) that nothing in production ever reached.

All three now share this module's vocabulary and its strict-coverage rule, so
"capability match" means exactly one thing across dispatch, reassignment and
auction bidding.

Vocabulary
----------
A *capability tag* is a lowercased, separator-normalised token. ``"Code
Generation"``, ``"code_generation"`` and ``"code-generation"`` are the same tag.
Normalisation is deliberately conservative: it collapses whitespace/hyphens and
trims, and nothing else, so a tag can never be silently invented.

Eligibility
-----------
:func:`eligible_candidates` is a **hard filter**, not a score. When a task
declares required tags, a candidate that does not cover all of them is removed
from the pool entirely and reported in ``rejected`` with the missing tags. This
is what makes "an agent that does not match is never chosen" a property of the
code rather than a property of a score threshold.

When a task declares no tags there is nothing to match on, so no capability
filter is applied and the caller records that fact explicitly in the dispatch
reason. Silently treating "no requirement" as "matches everything" is fine;
silently treating it as a match *without saying so* is not.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

#: Separators collapsed to ``_`` so ``"code generation"``/``"code-generation"``
#: and ``"code_generation"`` are one tag rather than three.
_SEPARATOR_RE = re.compile(r"[\s\-/]+")
_UNDERSCORE_RUN_RE = re.compile(r"_{2,}")

#: Hard ceiling on how many required tags one task may carry. A requirement set
#: this wide is almost always a mis-declaration, and an unbounded one would let
#: a single task become ineligible for the entire roster.
MAX_REQUIRED_TAGS = 12

#: Bot lifecycle states that may be handed work. Mirrors the pre-existing rule
#: used by :func:`alpha.bots.work_discovery.match_bot_for_task`.
ASSIGNABLE_BOT_STATUSES: frozenset[str] = frozenset({"active", "sleeping"})


class CapabilityProfile(Protocol):
    """Anything shaped like a :class:`alpha.bots.profile.BotProfile`.

    Declared as a Protocol so this module stays importable without pulling in
    the bot registry (and therefore without a cycle).
    """

    @property
    def name(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def capabilities(self) -> list[str]: ...

    @property
    def skills(self) -> list[str]: ...


def normalize_tag(raw: Any) -> str:
    """Normalise one capability tag to its canonical form.

    Empty/blank input normalises to ``""`` (never a placeholder), so a caller
    filtering on truthiness cannot accidentally match the empty tag.
    """
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    text = _SEPARATOR_RE.sub("_", text)
    text = _UNDERSCORE_RUN_RE.sub("_", text)
    return text.strip("_")


def normalize_tags(raw: Iterable[Any] | None) -> frozenset[str]:
    """Normalise a collection of tags to a de-duplicated frozenset."""
    if not raw:
        return frozenset()
    out = {normalize_tag(item) for item in raw}
    return frozenset(tag for tag in out if tag)


def profile_capability_tags(profile: CapabilityProfile) -> frozenset[str]:
    """The capability tags an agent *offers*.

    Declared ``capabilities`` and ``skills`` are both offered capabilities: a
    skill is a thing the agent can actually do, and leaving it out of the tag
    set made a skill-carrying specialist invisible to capability dispatch.
    ``responsibilities`` are prose descriptions, not capabilities, so they are
    deliberately excluded.
    """
    return normalize_tags([*(profile.capabilities or []), *(profile.skills or [])])


@dataclass(frozen=True)
class CapabilityMatch:
    """How well an agent covers a task's declared capability tags."""

    required: frozenset[str]
    offered: frozenset[str]
    matched: frozenset[str]
    missing: frozenset[str]
    constrained: bool

    @property
    def eligible(self) -> bool:
        """True when the agent may be handed this task.

        With no declared requirement there is nothing to fail, so every live
        agent is eligible and the caller must say so in its recorded reason.
        """
        return not self.constrained or not self.missing

    @property
    def coverage(self) -> float:
        """Fraction of the required tags this agent covers (1.0 when unconstrained)."""
        if not self.constrained or not self.required:
            return 1.0
        return round(len(self.matched) / len(self.required), 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": sorted(self.required),
            "matched": sorted(self.matched),
            "missing": sorted(self.missing),
            "constrained": self.constrained,
            "eligible": self.eligible,
            "coverage": self.coverage,
        }


def match_capabilities(required_tags: Iterable[Any] | None, profile: CapabilityProfile) -> CapabilityMatch:
    """Compare a task's required tags against one agent's offered tags."""
    required = normalize_tags(required_tags)
    if len(required) > MAX_REQUIRED_TAGS:
        raise ValueError(f"A task may declare at most {MAX_REQUIRED_TAGS} capability tags; got {len(required)}.")
    offered = profile_capability_tags(profile)
    matched = required & offered
    return CapabilityMatch(
        required=required,
        offered=offered,
        matched=matched,
        missing=required - matched,
        constrained=bool(required),
    )


# ---------------------------------------------------------------------------
# Objective -> required tags
# ---------------------------------------------------------------------------
#: Explicit keyword vocabulary mapping a task's TEXT to the capability tags that
#: text implies. This is a REQUIREMENT derivation, not a selection heuristic: its
#: only job is to answer "what capability does this task need?", and the answer
#: is then applied as a HARD filter.
#:
#: The error direction is the important part. A false positive makes a task
#: ineligible for everyone, which surfaces as an honest refusal with the missing
#: tag named — safe. A false negative leaves the task unconstrained and the
#: recorded reason says so. Neither direction can hand work to an agent that
#: does not declare the capability, because the filter downstream is strict.
#:
#: Extend this table rather than editing a call site: it is the one place a new
#: vocabulary term is added, and an operator can see the whole map at once.
CAPABILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sql": ("sql", "database", "schema", "query plan", "index", "postgres", "postgresql", "mysql", "sqlite", "migration", "join"),
    "python": ("python", "pytest", "pip", "django", "flask", "fastapi"),
    "typescript": ("typescript", "tsconfig", "node", "npm"),
    "backend": ("backend", "api", "endpoint", "service", "server", "rest", "graphql"),
    "code_generation": ("implement", "write the code", "refactor", "build the", "add a function", "port the"),
    "react": ("react", "component", "jsx", "nextjs", "hook", "state management"),
    "tailwind": ("tailwind", "css", "stylesheet", "design token"),
    "ui_design": ("ui", "ux", "layout", "responsive", "wireframe", "visual design"),
    "pytest": ("test", "tests", "pytest", "regression", "coverage", "flaky"),
    "test_automation": ("automate the test", "test suite", "harness the test"),
    "code_audit": ("audit", "smell", "lint", "style", "readability"),
    "security_review": ("security", "injection", "xss", "csrf", "vulnerab", "auth bypass", "secret leak"),
    "vulnerability_analysis": ("vulnerab", "cve", "attack surface", "threat model"),
    "web_search": ("search the web", "find sources", "literature", "survey the market", "look up"),
    "document_synthesis": ("summarise", "summarize", "synthesise", "synthesize", "write up"),
    "technical_writing": ("document", "readme", "documentation", "changelog", "release notes", "api docs"),
    "data_modeling": ("data model", "entity relationship", "normalis", "normaliz"),
    "performance_tuning": ("slow", "latency", "optimi", "profil", "throughput"),
    "system_design": ("architecture", "design the system", "boundary", "interface"),
    "incident_response": ("incident", "outage", "downtime", "on-call", "pager"),
    "docker": ("docker", "container image", "compose file"),
    "python_repl": ("repl", "notebook"),
}


def infer_capability_tags(objective: str, *, limit: int = 4) -> frozenset[str]:
    """Capability tags a task's own text requires.

    Returns at most ``limit`` tags, most-specific first, so one stray keyword
    cannot make a task ineligible for the whole roster. An empty result means the
    text stated no capability, and the caller must say so rather than pretend the
    task is unconstrained by accident.
    """
    text = (objective or "").lower()
    if not text.strip():
        return frozenset()
    hits: list[tuple[int, str]] = []
    for tag, keywords in CAPABILITY_KEYWORDS.items():
        for keyword in keywords:
            position = text.find(keyword)
            if position >= 0:
                # Earlier in the objective == more likely to be the point of it.
                hits.append((position, tag))
                break
    hits.sort()
    return frozenset(tag for _pos, tag in hits[: max(1, int(limit))])


def required_capability_tags(
    proposed_owners: Sequence[str],
    *,
    offered_by: dict[str, frozenset[str]] | None = None,
) -> tuple[frozenset[str], str]:
    """Derive the required tag set for work proposed for ``proposed_owners``.

    The plan proposes *who should own* a task; the capability requirement is the
    capability surface that owner(s) declare. For a single proposed owner that is
    exactly its declared tag set, so a bot that lacks any of those tags is
    unreachable — the point of the exercise.

    For several proposed owners the requirement is their **intersection**: the
    work is something any one of them could have done, so any one of them (or a
    better peer) may take it. An empty intersection means the plan proposed
    mutually disjoint specialists for one task, which carries no shared
    requirement; that is reported in the returned reason instead of being
    silently widened into "matches everyone".

    Returns ``(required_tags, reason)``.
    """
    surfaces: list[frozenset[str]] = []
    for owner in proposed_owners:
        key = str(owner or "").strip().lower()
        if not key:
            continue
        if offered_by is not None:
            offered = offered_by.get(key)
        else:
            offered = None
        if offered is None:
            try:
                from alpha.bots.registry import get_bot_registry

                profile = get_bot_registry().get_bot(key)
            except Exception:
                profile = None
            if profile is None:
                continue
            offered = profile_capability_tags(profile)
        surfaces.append(offered)

    if not surfaces:
        return frozenset(), "no proposed owner resolved to a roster profile; no capability constraint applied"
    if len(surfaces) == 1:
        return (
            surfaces[0],
            "the task text stated no capability, so the required tags are the proposed owner's declared surface; "
            "this is a FALLBACK, not a capability match on the task",
        )
    shared = frozenset.intersection(*surfaces)
    if not shared:
        return (
            frozenset(),
            "proposed owners declare disjoint capability sets; no shared requirement, so no capability constraint applied",
        )
    return shared, "required tags are the capability surface shared by every proposed owner"


@dataclass
class EligibilityResult:
    """The outcome of filtering a roster down to the agents allowed to do a task."""

    required: frozenset[str] = frozenset()
    constrained: bool = False
    eligible: list[str] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": sorted(self.required),
            "constrained": self.constrained,
            "eligible": list(self.eligible),
            "rejected": [dict(item) for item in self.rejected],
            "reason": self.reason,
        }


def eligible_candidates(
    profiles: Iterable[CapabilityProfile],
    required_tags: Iterable[Any] | None,
    *,
    exclude: Iterable[str] | None = None,
) -> EligibilityResult:
    """Filter ``profiles`` to the agents eligible for a task, with the reasons.

    A candidate is dropped when it is suspended/archived, is explicitly excluded
    (e.g. already in this task's delegation lineage, or the agent that just
    failed), or does not cover the required capability tags. Every drop is
    reported so the dispatcher's recorded reason can explain the whole decision,
    not just the winner.
    """
    required = normalize_tags(required_tags)
    if len(required) > MAX_REQUIRED_TAGS:
        raise ValueError(f"A task may declare at most {MAX_REQUIRED_TAGS} capability tags; got {len(required)}.")
    excluded = {str(item or "").strip().lower() for item in (exclude or []) if str(item or "").strip()}
    constrained = bool(required)

    eligible: list[str] = []
    rejected: list[dict[str, Any]] = []
    for profile in profiles:
        name = str(getattr(profile, "name", "") or "").strip().lower()
        status = str(getattr(profile, "status", "") or "").strip().lower()
        if name in excluded:
            rejected.append({"bot": name, "code": "excluded", "detail": "agent is on the exclusion list for this dispatch"})
            continue
        if status not in ASSIGNABLE_BOT_STATUSES:
            rejected.append({"bot": name, "code": "not_assignable", "detail": f"status '{status}' cannot receive work"})
            continue
        match = match_capabilities(required, profile)
        if not match.eligible:
            rejected.append(
                {
                    "bot": name,
                    "code": "capability_mismatch",
                    "detail": f"missing capability tags: {', '.join(sorted(match.missing))}",
                    "missing": sorted(match.missing),
                }
            )
            continue
        eligible.append(name)

    if not constrained:
        reason = "no capability tags declared for this task; every live agent stayed eligible and selection fell to the auction"
    else:
        reason = f"capability match: required {sorted(required)}"
    return EligibilityResult(required=required, constrained=constrained, eligible=eligible, rejected=rejected, reason=reason)
