"""Deterministic workspace survey for newly forged Bots.

A Bot that is created from one sentence arrives knowing its job and nothing
about the machine it landed on, so the first thing an operator does is explain
their own workspace to it. This module reads that context **once, at birth**
and returns a small structured answer that the forge writes into the Bot's
memory, so the Bot knows where it stands on turn one and never researches the
machine again.

Design contract (deliberate, and load-bearing):

* **Deterministic word matching, not a model call.** No tokens, no latency, no
  questions. The same inputs always produce the same output.
* **Read-only and shallow.** Directory names, git remotes, and the *head* of a
  README / AGENTS.md / CLAUDE.md. Never source files, never inside dependency
  trees, never the home directory unless the operator names it in
  ``workspace_roots``.
* **Bounded.** A wall-clock budget caps the whole scan, and results are cached
  so the tenth Bot costs nothing.
* **Credential-safe.** Anything that looks like a secret is stripped before it
  can be returned or written into a Bot's memory.

The survey answers three questions:

``fits``
    Which existing workspace/repo best matches the Bot's own SOUL + role.
``covered_by``
    Whether an existing Bot already works this territory (used by the
    duplicate-role refusal in :mod:`alpha.bots.forge`).
``skills_here``
    Which installed skills already cover the territory.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "WorkspaceFinding",
    "SurveyResult",
    "survey_workspace",
    "clear_survey_cache",
    "score_overlap",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Filenames whose *head* we are willing to read for context.
CONTEXT_FILES = ("README.md", "AGENTS.md", "CLAUDE.md", "readme.md")

#: Never descend into these directory names.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        ".cache",
        "target",
        "vendor",
        ".idea",
        ".vscode",
        "site-packages",
    }
)

#: How many bytes of a context file we read. The *head*, never the whole file.
CONTEXT_HEAD_BYTES = 4096

#: Maximum directories visited before the survey stops walking.
MAX_WALK = 400

#: Hard wall-clock budget for a full scan, in seconds.
DEFAULT_BUDGET_SECONDS = 3.0

#: Cache TTL in seconds.
DEFAULT_CACHE_TTL = 600.0

#: Operator kill switch for the whole survey. Set ``AGENT_WORKSPACE_BOT_SURVEY=0``
#: to stop the forge from reading the workspace at a Bot's birth. Defaults to on.
SURVEY_KILL_SWITCH = "AGENT_WORKSPACE_BOT_SURVEY"

_OFF_VALUES = frozenset({"0", "false", "off", "no", "disabled"})

#: What a disabled survey is called, so callers can tell it from a real one.
DISABLED_REASON = "survey disabled by operator"


def _survey_disabled() -> bool:
    """Whether the operator has switched the workspace survey off."""
    return os.environ.get(SURVEY_KILL_SWITCH, "1").strip().lower() in _OFF_VALUES


#: Words that carry no discriminative signal when scoring.
_STOPWORDS = frozenset(
    """
    a an and are as at be but by for from has have if in into is it its of on
    or that the this to was we will with you your our not can all any do does
    done new get make using use used over under than then them they he she
    i me my mine who what when where how why which also more most other some
    such only own same so too very just about across after before between
    during through up down out off again further once here there each few
    both no nor own s t don now
    """.split()
)

#: Shape of something that must never reach a Bot's memory.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|ghu|xox[baprs])[-_][A-Za-z0-9\-_]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:api[_-]?key|apikey|secret|token|passwd|password)\s*[:=]\s*\S+", re.I),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\b[0-9a-f]{32,}\b", re.I),
)

#: Characters that make a string unreadable / non-discriminative.
_NOISE_RE = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceFinding:
    """One surveyed location (a repo or directory under a workspace root)."""

    path: str
    kind: str  # "root" | "repo"
    name: str
    score: float = 0.0
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "name": self.name,
            "score": round(self.score, 3),
            "evidence": list(self.evidence),
        }


@dataclass
class SurveyResult:
    """The answer written into a forged Bot's memory."""

    fits: str | None = None
    fits_path: str | None = None
    fits_score: float = 0.0
    already_here: list[str] = field(default_factory=list)
    covered_by: str | None = None
    skills_here: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    roots_scanned: list[str] = field(default_factory=list)
    findings: list[WorkspaceFinding] = field(default_factory=list)
    truncated: bool = False
    elapsed_seconds: float = 0.0
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "fits": self.fits,
            "fits_path": self.fits_path,
            "fits_score": round(self.fits_score, 3),
            "already_here": list(self.already_here),
            "covered_by": self.covered_by,
            "skills_here": list(self.skills_here),
            "next_steps": list(self.next_steps),
            "roots_scanned": list(self.roots_scanned),
            "findings": [f.to_dict() for f in self.findings],
            "truncated": self.truncated,
            "elapsed_seconds": round(self.elapsed_seconds, 4),
            "cached": self.cached,
        }

    def clone(self) -> SurveyResult:
        """A detached copy safe to hand to a caller.

        Deliberately *not* built from :meth:`to_dict`: that round-trip turns
        each :class:`WorkspaceFinding` into a plain dict, and a cached result
        rebuilt that way crashes the moment anyone reads ``findings``. Findings
        are frozen, so sharing the element objects between cache and caller is
        safe while the lists themselves are copied.
        """
        return SurveyResult(
            fits=self.fits,
            fits_path=self.fits_path,
            fits_score=self.fits_score,
            already_here=list(self.already_here),
            covered_by=self.covered_by,
            skills_here=list(self.skills_here),
            next_steps=list(self.next_steps),
            roots_scanned=list(self.roots_scanned),
            findings=list(self.findings),
            truncated=self.truncated,
            elapsed_seconds=self.elapsed_seconds,
            cached=self.cached,
        )

    def memory_block(self) -> str:
        """The compact, credential-safe text written into Bot memory.

        Deliberately short: the Bot reads this once and never re-researches.
        """
        lines: list[str] = ["## Where you landed"]
        if self.fits:
            lines.append(f"- fits: {self.fits} ({self.fits_path})")
        else:
            lines.append("- fits: none identified — ask the operator where this work belongs")
        if self.already_here:
            lines.append(f"- already here: {', '.join(self.already_here)}")
        if self.covered_by:
            lines.append(f"- heads up: @{self.covered_by} already works in this territory")
        if self.skills_here:
            lines.append(f"- skills here: {', '.join(self.skills_here)}")
        for step in self.next_steps[:5]:
            lines.append(f"- next: {step}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tokenisation / scoring
# ---------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens with stopwords and pure-noise removed."""
    out: list[str] = []
    for raw in _NOISE_RE.split((text or "").lower()):
        if len(raw) < 3 or raw in _STOPWORDS or raw.isdigit():
            continue
        out.append(raw)
    return out


def _digest(text: str) -> dict[str, int]:
    """Term-frequency digest of ``text``."""
    counts: dict[str, int] = {}
    for tok in _tokens(text):
        counts[tok] = counts.get(tok, 0) + 1
    return counts


def _overlap_score(query: dict[str, int], candidate: dict[str, int]) -> tuple[float, list[str]]:
    """Weighted overlap between a query and a candidate digest.

    Rare (more specific) terms contribute more than common ones, so a Bot whose
    role says "invoice reconciliation" outranks a directory merely called
    "work". Returns ``(score, evidence)`` where evidence names the strongest
    matching terms — the operator can see *why* a fit was chosen.
    """
    if not query or not candidate:
        return 0.0, []
    # Inverse document frequency across the candidate is approximated by how
    # rare the term is *within the query*: specific words dominate.
    scored: list[tuple[int, str]] = []
    for term, qn in query.items():
        cn = candidate.get(term)
        if not cn:
            continue
        weight = 1 + len(term) // 4  # longer terms are more specific
        scored.append((min(qn, cn) * weight, term))
    if not scored:
        return 0.0, []
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    raw = sum(s for s, _ in scored)
    # Normalise against the query's own maximum possible weight so scores stay
    # comparable across Bot roles of different lengths.
    ceiling = sum((1 + len(t) // 4) * n for t, n in query.items())
    score = raw / ceiling if ceiling else 0.0
    evidence = tuple(term for _, term in scored[:6])
    return score, list(evidence)


def score_overlap(query_text: str, candidate_text: str) -> tuple[float, list[str]]:
    """Public wrapper: score how well ``candidate_text`` matches ``query_text``."""
    return _overlap_score(_digest(query_text), _digest(candidate_text))


#: Distinct significant words a query needs before overlap may *refuse* work.
#:
#: The score is normalised against the query's own ceiling, so a very short
#: role is scored against a very short ceiling: the one-word role "Security"
#: scores 1.0 against any SOUL that merely contains "security", and a
#: refusal built on that would be arbitrary. Three distinct words is the point
#: where the score starts meaning "these describe the same job" rather than
#: "these share a word".
MIN_OVERLAP_TERMS = 3


def significant_terms(text: str) -> set[str]:
    """Distinct significant words in ``text`` — the query mass behind a score."""
    return set(_digest(text))


# ---------------------------------------------------------------------------
# Secret scrubbing
# ---------------------------------------------------------------------------


def scrub_secrets(text: str) -> str:
    """Strip credential-shaped content from text headed for Bot memory.

    The survey reads README heads and git remotes; either can legitimately
    contain a token example. Nothing credential-shaped may reach a Bot.
    """
    if not text:
        return ""
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned


def looks_like_secret(text: str) -> bool:
    """True when ``text`` contains credential-shaped content."""
    if not text:
        return False
    return any(p.search(text) for p in _SECRET_PATTERNS)


# ---------------------------------------------------------------------------
# Filesystem scanning
# ---------------------------------------------------------------------------


def _read_head(path: Path) -> str:
    """Read at most :data:`CONTEXT_HEAD_BYTES` from a context file."""
    try:
        with path.open("rb") as fh:
            raw = fh.read(CONTEXT_HEAD_BYTES)
        return scrub_secrets(raw.decode("utf-8", errors="replace"))
    except OSError:
        return ""


def _git_remote(path: Path) -> str:
    """Best-effort origin URL without shelling out (read-only, no subprocess)."""
    head = path / ".git" / "HEAD"
    try:
        if not head.is_file():
            return ""
        gitdir = path / ".git"
        if gitdir.is_file():  # worktree / submodule pointer
            body = gitdir.read_text("utf-8", errors="replace").strip()
            if body.startswith("gitdir:"):
                target = (path / body[7:].strip()).resolve()
                gitdir = target
        cfg = gitdir / "config"
        if cfg.is_file():
            for line in cfg.read_text("utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("url"):
                    _, _, value = line.partition("=")
                    return value.strip()
    except OSError:
        pass
    return ""


def _is_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _discover_roots(cwd: str | None, workspace_roots: list[str] | None) -> list[Path]:
    """Resolve which directories are in scope.

    The home directory is only ever scanned when the operator names it
    explicitly in ``workspace_roots`` — never by default.
    """
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(candidate: str | Path | None) -> None:
        if not candidate:
            return
        try:
            resolved = Path(candidate).expanduser().resolve()
        except (OSError, RuntimeError):
            return
        if not resolved.is_dir():
            return
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        roots.append(resolved)

    _add(cwd or os.getcwd())
    for extra in workspace_roots or []:
        _add(extra)
    return roots


def _walk(root: Path, deadline: float) -> tuple[list[Path], bool]:
    """Collect repo/directories under ``root``, bounded by time and count."""
    found: list[Path] = []
    truncated = False
    stack: list[tuple[Path, int]] = [(root, 0)]
    visited = 0
    while stack:
        if visited >= MAX_WALK or time.monotonic() > deadline:
            truncated = True
            break
        current, depth = stack.pop()
        visited += 1
        if depth > 0:
            found.append(current)
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            if child.name in SKIP_DIRS or child.name.startswith("."):
                continue
            # A repo is a boundary: record it, do not descend into it.
            if _is_repo(child):
                found.append(child)
                continue
            if depth < 3:
                stack.append((child, depth + 1))
    return found, truncated


def _describe(path: Path, root: Path) -> str:
    """Corpus describing a location: name, remote, and the head of its docs."""
    parts = [path.name, root.name]
    remote = _git_remote(path)
    if remote:
        parts.append(remote)
    for filename in CONTEXT_FILES:
        doc = path / filename
        if doc.is_file():
            parts.append(_read_head(doc))
    return scrub_secrets("\n".join(parts))


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, SurveyResult]] = {}
_CACHE_LOCK = threading.Lock()


def clear_survey_cache() -> None:
    """Drop every cached survey (tests and operators changing workspace)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_key(cwd: str | None, roots: list[str] | None, query: str) -> str:
    payload = "|".join(
        [
            cwd or "",
            ",".join(sorted(roots or [])),
            hashlib.sha256((query or "").encode("utf-8")).hexdigest(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cached(key: str, ttl: float) -> SurveyResult | None:
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
    if not entry:
        return None
    stamped, value = entry
    if time.monotonic() - stamped > ttl:
        return None
    clone = value.clone()
    clone.cached = True
    return clone


def _store(key: str, value: SurveyResult) -> None:
    with _CACHE_LOCK:
        # Bounded cache: keep it from growing without limit in a long-lived
        # gateway process.
        if len(_CACHE) > 128:
            _CACHE.clear()
        _CACHE[key] = (time.monotonic(), value)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def survey_workspace(
    soul: str,
    *,
    role: str = "",
    name: str = "",
    cwd: str | None = None,
    workspace_roots: list[str] | None = None,
    skills: list[str] | None = None,
    existing_roles: dict[str, str] | None = None,
    installed_skills: list[str] | None = None,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    cache_ttl: float = DEFAULT_CACHE_TTL,
    use_cache: bool = True,
) -> SurveyResult:
    """Survey the workspace a new Bot was born into.

    Args:
        soul: The Bot's SOUL.md text — the primary thing being matched.
        role: The Bot's role line (folded into the query).
        name: The Bot's name (folded into the query).
        cwd: Directory the agent is running in. Defaults to ``os.getcwd()``.
        workspace_roots: Extra roots to survey. The home directory is only
            scanned when named here.
        skills: Skills declared on the new Bot.
        existing_roles: ``{bot_name: role/soul snippet}`` for Bots already on
            this install. Used to report territory that is already covered.
        installed_skills: Every skill name installed on this install.
        budget_seconds: Hard wall-clock cap for the scan.
        cache_ttl: How long a result stays fresh.
        use_cache: Set ``False`` to force a rescan.

    Returns:
        A :class:`SurveyResult`. Never raises for an unreadable workspace —
        an unreadable location simply produces no finding.
    """
    started = time.monotonic()
    query = "\n".join(x for x in (name, role, soul) if x)

    if _survey_disabled():
        # Operator kill switch, checked before the cache so a disabled survey
        # can never be mistaken for (or be served as) a real one.
        return SurveyResult(next_steps=["workspace survey disabled by the operator (AGENT_WORKSPACE_BOT_SURVEY=0) - ask where this work belongs"])

    key = _cache_key(cwd, workspace_roots, query)
    if use_cache:
        hit = _cached(key, cache_ttl)
        if hit is not None:
            hit.elapsed_seconds = time.monotonic() - started
            return hit

    deadline = started + max(0.1, budget_seconds)
    roots = _discover_roots(cwd, workspace_roots)

    result = SurveyResult(roots_scanned=[str(r) for r in roots])
    query_digest = _digest(query)

    query_terms = set(query_digest)
    # A thin query cannot support a "already covered" verdict either: the score
    # is normalised against the query's own ceiling, so one shared word would
    # otherwise look like a full match.
    overlap_judgementable = len(query_terms) >= MIN_OVERLAP_TERMS
    already_here: list[str] = []
    covered_by: str | None = None
    best_overlap = 0.0

    # ---- existing bots: territory that is already claimed -------------
    for bot_name, snippet in (existing_roles or {}).items():
        score, terms = _overlap_score(query_digest, _digest(snippet))
        if overlap_judgementable and score > best_overlap and score >= 0.34:
            best_overlap = score
            covered_by = bot_name
        elif score > best_overlap:
            best_overlap = score
        if score >= 0.20:
            already_here.append(bot_name)

    # ---- installed skills ---------------------------------------------
    skill_hits: list[str] = []
    for skill in installed_skills or []:
        if not skill:
            continue
        score, _ = _overlap_score(query_digest, _digest(skill.replace("-", " ").replace("_", " ")))
        if score >= 0.18 or any(t in skill.lower() for t in query_terms if len(t) > 4):
            skill_hits.append(skill)
    result.skills_here = sorted(set(skill_hits))[:8]
    result.already_here = sorted(set(already_here))
    result.covered_by = covered_by

    # ---- filesystem -----------------------------------------------------
    findings: list[WorkspaceFinding] = []
    for root in roots:
        if time.monotonic() > deadline:
            result.truncated = True
            break
        corpus = _describe(root, root)
        score, terms = _overlap_score(query_digest, _digest(corpus))
        findings.append(
            WorkspaceFinding(
                path=str(root),
                kind="root",
                name=root.name,
                score=score,
                evidence=tuple(terms),
            )
        )
        if _is_repo(root):
            continue  # a root that is itself a repo needs no deeper walk
        candidates, truncated = _walk(root, deadline)
        result.truncated = result.truncated or truncated
        for candidate in candidates:
            if time.monotonic() > deadline:
                result.truncated = True
                break
            candidate_corpus = _describe(candidate, root)
            cand_score, cand_terms = _overlap_score(query_digest, _digest(candidate_corpus))
            findings.append(
                WorkspaceFinding(
                    path=str(candidate),
                    kind="repo" if _is_repo(candidate) else "dir",
                    name=candidate.name,
                    score=cand_score,
                    evidence=tuple(cand_terms),
                )
            )

    findings.sort(key=lambda f: (-f.score, f.path))
    result.findings = findings[:12]

    # ---- decide "fits" ---------------------------------------------------
    # Require a real signal: a name that merely echoes the Bot's own title is
    # circular, so a finding must beat a small floor to be called a fit.
    for finding in result.findings:
        if finding.score >= 0.15 and finding.score >= best_overlap:
            result.fits = finding.name
            result.fits_path = finding.path
            result.fits_score = finding.score
            break

    # ---- next steps -------------------------------------------------------
    if result.fits is None:
        result.next_steps.append("no workspace matched this role — ask the operator where the work belongs")
    if covered_by:
        result.next_steps.append(f"coordinate with @{covered_by}: overlapping territory detected")
    if not result.skills_here:
        result.next_steps.append("no installed skill covers this role yet")
    if result.truncated:
        result.next_steps.append("survey hit its time/count budget — only a partial scan was returned")

    result.elapsed_seconds = time.monotonic() - started
    if use_cache:
        _store(key, result)

    # Return a detached copy so callers cannot mutate the cache entry.
    return result.clone()
