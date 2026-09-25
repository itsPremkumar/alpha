"""Evaluator integrity: refuse a change that edited the judge.

Alpha's self-evolution surfaces (``alpha/rsi``, ``alpha/evolution``,
``scripts/auto_update.py``, skill evolution) all end in a *judgement*. This
module is the part that asks the question nobody asks: **did the proposal
change the thing that judges it?**

A candidate that lowers a threshold, deletes the failing case, adds
``pytest.mark.skip``, swaps ``==`` for ``math.isclose``, or simply rewrites the
comparison function can "improve" any metric it likes. The literature on
evaluator integrity and specification gaming (Amodei et al., *Concrete Problems
in AI Safety*; Krakovna et al., *Specification Gaming*; the repair literature
on agents that patch their own benchmarks) all reach the same conclusion: a
measured improvement produced by a weakened judge is not evidence of anything.

This module therefore answers conservatively:

* every path a proposal touches is classified against an **injected**
  :class:`IntegrityPolicy` of evaluation-surface globs (defaulting to the real
  surfaces found in this repository);
* any change to an evaluation surface produces a finding - an *unexplained*
  change is ``suspect``, never silently allowed;
* a *specific weakening* (deleted test, added skip/xfail/tolerance marker,
  loosened assertion, deleted case, changed threshold/metric/baseline, or a
  fingerprint mismatch against the injected baseline) is a ``blocker`` and
  moves the report to ``compromised``;
* this package treats **its own** files as an evaluation surface, so the gate
  cannot approve itself by editing its own decision logic
  (see :data:`SELF_PACKAGE_GLOBS` and ``gate_self_modified``).

No self-reference escape: the policy object is constructed from constants and
operator config, never from proposal input, and the classification order is
fixed. Extending the policy is allowed (an operator may add globs); *removing*
one is not expressible through this API.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from alpha.evolution.evidence.models import INTEGRITY_STATUSES, EvaluationIntegrityReport, IntegrityFinding

__all__ = [
    "BLOCKING_CATEGORIES",
    "CHANGE_TYPES",
    "DEFAULT_INTEGRITY_PATH_GLOBS",
    "GATE_SELF_CATEGORY",
    "INTEGRITY_CATEGORY_ORDER",
    "INTEGRITY_STATUSES",
    "SELF_JUDGE_FILES",
    "SELF_PACKAGE_GLOBS",
    "SKIP_MARKERS",
    "TOLERANCE_MARKERS",
    "ChangedPath",
    "IntegrityPolicy",
    "check_evaluator_integrity",
    "default_integrity_path_globs",
    "default_integrity_policy",
    "glob_matches",
]

#: Closed set of change kinds a caller may report for a touched path.
CHANGE_TYPES: tuple[str, ...] = ("added", "modified", "deleted", "renamed")

#: This package's own files are part of the evaluation surface. Editing the
#: decision logic must be *detected*, never self-approved.
SELF_PACKAGE_GLOBS: tuple[str, ...] = ("backend/packages/harness/alpha/evolution/evidence/**/*",)

#: Files in this package whose edit would rewrite the gate itself. Named
#: explicitly so a finding can say *which* part of the judge was touched.
SELF_JUDGE_FILES: dict[str, str] = {
    "decision.py": "the verdict decision table",
    "integrity.py": "the evaluator-integrity policy itself",
    "compare.py": "the noise-floor comparison logic",
    "gates.py": "the required precondition gates",
    "measure.py": "the measurement contract",
    "models.py": "the closed status sets and data contracts",
    "config.py": "the evidence-gate configuration (thresholds and required gates)",
    "provenance.py": "the append-only provenance record",
    "service.py": "the gate entry point",
    "__init__.py": "the package's public surface",
}

#: Markers that disable a test/case. Added on an evaluation surface they are a
#: blocker: the judge stopped judging.
SKIP_MARKERS: tuple[str, ...] = (
    "pytest.mark.skip",
    "pytest.mark.xfail",
    "unittest.skip",
    "@skip",
    "skipif",
    "skip(",
    "no cover: gate disabled",
    "xfail(",
)

#: Markers that turn an exact assertion into a tolerant one.
TOLERANCE_MARKERS: tuple[str, ...] = (
    "pytest.approx",
    "math.isclose",
    "isclose(",
    "approx=",
    "rel=",
    "abs=",
    "tolerance=",
    "tolerance:",
    "atol=",
    "rtol=",
    "range(0,",
    "round(",
)

#: A removed test function is a deleted case; there is no weaker reading.
_TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)", re.IGNORECASE)
_TEST_CLASS_RE = re.compile(r"^\s*class\s+(Test\w*)")
_ASSERT_RE = re.compile(r"\bassert\b|\bself\.assert|\.assert[A-Z]")
_EXACT_OPERATOR_RE = re.compile(r"(==|!=|\bis\b)")
_LOOSE_OPERATOR_RE = re.compile(r"(>=|<=|>|<|\bin\b|\bnot in\b|contains|startswith|endswith)")

#: Category ordering: the first matching category wins, so a path that is both
#: "a test" and "part of the rsi evaluator surface" is reported as a test (the
#: more specific reason).
INTEGRITY_CATEGORY_ORDER: tuple[str, ...] = (
    "gate_self",
    "metric_implementations",
    "benchmark_cases",
    "tests",
    "threshold_config",
    "gate_scripts",
    "baselines",
    "rsi_evaluator_surface",
)

GATE_SELF_CATEGORY = "gate_self"

#: Default evaluation-surface globs: the real surfaces in this repository.
#: Every entry was verified to exist (or to be a directory) in the checkout.
DEFAULT_INTEGRITY_PATH_GLOBS: dict[str, tuple[str, ...]] = {
    "tests": (
        "backend/tests/**/*",
        "tests/**/*",
    ),
    "threshold_config": (
        "backend/ruff.toml",
        "backend/packages/harness/pyproject.toml",
        "config.example.yaml",
        "config/update-policy.json",
    ),
    "benchmark_cases": (
        "backend/packages/harness/alpha/benchmarks/**/*",
        "backend/packages/harness/alpha/memory/evaluation/cases/**/*",
        "backend/scripts/benchmark/**/*",
    ),
    "metric_implementations": (
        "backend/packages/harness/alpha/memory/evaluation/metrics.py",
        "backend/packages/harness/alpha/memory/evaluation/suite.py",
        "backend/packages/harness/alpha/memory/evaluation/models.py",
        "backend/packages/harness/alpha/memory/evaluation/config.py",
        "backend/packages/harness/alpha/benchmarks/release_gate.py",
        "backend/packages/harness/alpha/benchmarks/runner.py",
        "backend/packages/harness/alpha/rsi/holdout.py",
        "backend/packages/harness/alpha/rsi/evaluator_manifest.py",
        "backend/packages/harness/alpha/rsi/protected_paths.py",
        "backend/packages/harness/alpha/rsi/review.py",
        "backend/packages/harness/alpha/evolution/engine.py",
    ),
    "gate_scripts": (
        "scripts/audit_dup_expressions.py",
        "scripts/audit_env_wiring.py",
        "scripts/check_agent_guidance.py",
        "scripts/prod_check.py",
    ),
    "baselines": (
        "**/baseline*.json",
        "**/*-baseline.json",
    ),
    "gate_self": SELF_PACKAGE_GLOBS,
    "rsi_evaluator_surface": (),
}

#: Categories whose *any* modification is a blocker (the judge or its
#: thresholds moved), as opposed to categories where a mere unexplained change
#: is only ``suspect``.
BLOCKING_CATEGORIES: dict[str, str] = {
    "gate_self": "the evidence gate's own decision logic was modified",
    "metric_implementations": "the implementation of a measured metric was modified",
    "threshold_config": "a configured threshold was modified",
    "gate_scripts": "a repository gate script was modified",
    "baselines": "a recorded baseline was modified",
}


def default_integrity_path_globs() -> dict[str, tuple[str, ...]]:
    """Return the default evaluation-surface globs, including RSI's own set.

    ``alpha.rsi.evaluator_manifest.EVALUATOR_SURFACE`` is **imported**, never
    copied, so this package cannot drift from the surface RSI already protects
    (tests, benchmark suites, release gate, safety guards, reproduction gates,
    policy engine). The import is function-local: ``alpha.rsi`` eagerly loads the
    RSI engine, and this module must stay cheap to import. An ``ImportError``
    propagates - a gate that cannot resolve the evaluation surface fails closed
    at the caller rather than silently protecting nothing.
    """

    from alpha.rsi.evaluator_manifest import EVALUATOR_SURFACE

    globs: dict[str, tuple[str, ...]] = {name: tuple(patterns) for name, patterns in DEFAULT_INTEGRITY_PATH_GLOBS.items()}
    merged = tuple(dict.fromkeys((*globs["rsi_evaluator_surface"], *EVALUATOR_SURFACE)))
    globs["rsi_evaluator_surface"] = merged
    return globs


_ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")


def _normalize(path: Any) -> str:
    """Normalize a path to a ``/``-separated string without touching the disk."""

    text = str(path or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


@lru_cache(maxsize=512)
def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Translate one ``/``-separated glob into an anchored regex.

    ``**/`` matches zero or more leading segments, ``**`` any characters, ``*``
    any characters inside one segment, ``?`` one non-separator character.
    Everything else is literal.
    """

    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**/", index):
                out.append("(?:.*/)?")
                index += 3
                continue
            if pattern.startswith("**", index):
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
            index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    return re.compile(f"^{''.join(out)}$")


def _suffixes(normalized: str) -> tuple[str, ...]:
    """The full path plus every trailing segment suffix (longest first)."""

    parts = [part for part in normalized.split("/") if part]
    return tuple("/".join(parts[index:]) for index in range(len(parts)))


def glob_matches(pattern: str, path: Any) -> bool:
    """Return whether ``path`` matches ``pattern`` (repo-relative or absolute).

    A repo-relative path is matched **as written**: a relative path such as
    ``skills/tests/helpers.py`` must not be pulled onto the ``backend/tests/…``
    surface just because its tail looks familiar.

    An *absolute* path additionally gets suffix matching, so a candidate that
    reports ``C:/checkout/backend/tests/test_x.py`` (or a worktree path with a
    prefix) cannot dodge the evaluation surface by carrying a prefix.
    """

    raw = str(path or "").strip().replace("\\", "/")
    normalized = _normalize(raw)
    if not normalized:
        return False
    regex = _compile_glob(_normalize(pattern))
    if regex.match(normalized):
        return True
    if _ABSOLUTE_RE.match(raw):
        return any(regex.match(suffix) for suffix in _suffixes(normalized)[1:])
    return False


@dataclass(frozen=True)
class ChangedPath:
    """One path a proposal touched, with the real diff evidence for it.

    ``added_lines``/``removed_lines`` are the *added* and *removed* source
    lines of the change (no patch parsing happens here - the caller owns the
    diff). They are what lets the checker say "a skip marker was added" or "a
    ``test_*`` function was removed" instead of merely "this file changed".
    """

    path: str
    change_type: str = "modified"
    added_lines: tuple[str, ...] = ()
    removed_lines: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized = _normalize(self.path)
        if not normalized:
            raise ValueError("changed path must be a non-empty path")
        object.__setattr__(self, "path", normalized)
        if self.change_type not in CHANGE_TYPES:
            raise ValueError(f"change_type must be one of {list(CHANGE_TYPES)}, got {self.change_type!r}")
        object.__setattr__(self, "added_lines", tuple(str(line) for line in self.added_lines))
        object.__setattr__(self, "removed_lines", tuple(str(line) for line in self.removed_lines))

    @property
    def deleted(self) -> bool:
        return self.change_type == "deleted"

    @classmethod
    def coerce(cls, value: ChangedPath | str) -> ChangedPath:
        if isinstance(value, ChangedPath):
            return value
        if isinstance(value, str):
            return cls(path=value)
        raise TypeError(f"changed path must be a ChangedPath or str, got {type(value).__name__}")


@dataclass(frozen=True)
class IntegrityPolicy:
    """Which path globs count as evaluation surfaces, and how they are judged.

    The policy is *injectable* (tests and operators may add globs) but its
    behaviour is fixed:

    * classification walks :data:`INTEGRITY_CATEGORY_ORDER` and returns the
      first matching category, so the reason recorded for a path is the most
      specific one;
    * a path that matches no glob is simply not part of the surface (an
      ordinary source edit is not tampering);
    * a path that matches a glob always produces at least a ``suspect``
      finding, so an unexplained change to the surface is never silently
      allowed;
    * marker patterns can be **extended** (``extra_skip_markers`` /
      ``extra_tolerance_markers``) but the defaults can never be removed.
    """

    path_globs: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: default_integrity_path_globs())
    extra_skip_markers: tuple[str, ...] = ()
    extra_tolerance_markers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized: dict[str, tuple[str, ...]] = {}
        for name, patterns in dict(self.path_globs).items():
            key = _require_category(name)
            normalized[key] = tuple(dict.fromkeys(_normalize(pattern) for pattern in patterns if _normalize(pattern)))
        unknown = sorted(set(normalized) - set(INTEGRITY_CATEGORY_ORDER))
        if unknown:
            raise ValueError(f"unknown integrity categories {unknown}; allowed categories are {list(INTEGRITY_CATEGORY_ORDER)}")
        object.__setattr__(self, "path_globs", normalized)
        object.__setattr__(self, "extra_skip_markers", tuple(str(marker) for marker in self.extra_skip_markers))
        object.__setattr__(self, "extra_tolerance_markers", tuple(str(marker) for marker in self.extra_tolerance_markers))

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> IntegrityPolicy:
        """Build a policy from a plain ``{category: [globs]}`` mapping."""

        if not isinstance(values, Mapping):
            raise TypeError(f"integrity path globs must be a mapping, got {type(values).__name__}")
        return cls(path_globs={str(name): tuple(str(item) for item in patterns) for name, patterns in values.items()})

    @classmethod
    def from_config(cls, config: Any) -> IntegrityPolicy:
        """Build a policy from an :class:`EvolutionEvidenceConfig` (duck-typed).

        Only ``config.integrity_path_globs`` is read here, so the policy stays a
        pure function of the operator-declared surface and never of proposal
        input.
        """

        return cls.from_mapping(config.integrity_path_globs)

    def globs_for(self, category: str) -> tuple[str, ...]:
        return tuple(self.path_globs.get(category, ()))

    @property
    def skip_markers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*SKIP_MARKERS, *self.extra_skip_markers)))

    @property
    def tolerance_markers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*TOLERANCE_MARKERS, *self.extra_tolerance_markers)))

    def classify(self, path: Any) -> str | None:
        """Return the evaluation-surface category for ``path`` (``None`` = not a surface)."""

        for category in INTEGRITY_CATEGORY_ORDER:
            for pattern in self.globs_for(category):
                if glob_matches(pattern, path):
                    return category
        return None

    def is_surface(self, path: Any) -> bool:
        return self.classify(path) is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_globs": {name: list(patterns) for name, patterns in self.path_globs.items()},
            "skip_markers": list(self.skip_markers),
            "tolerance_markers": list(self.tolerance_markers),
        }


def default_integrity_policy() -> IntegrityPolicy:
    """Return the default policy (the real surfaces in this repository)."""

    return IntegrityPolicy()


def _require_category(name: Any) -> str:
    text = str(name or "").strip()
    if not text:
        raise ValueError("integrity category name must be a non-empty string")
    return text


def _marker_hits(lines: Sequence[str], markers: Sequence[str]) -> list[str]:
    hits: list[str] = []
    for line in lines:
        lowered = line.lower()
        for marker in markers:
            if marker.lower() in lowered and marker not in hits:
                hits.append(marker)
    return hits


def _assertion_lines(lines: Sequence[str]) -> list[str]:
    return [line for line in lines if _ASSERT_RE.search(line)]


def _removed_test_names(lines: Sequence[str]) -> list[str]:
    names: list[str] = []
    for line in lines:
        match = _TEST_DEF_RE.match(line) or _TEST_CLASS_RE.match(line)
        if match:
            name = match.group(1)
            if name not in names:
                names.append(name)
    return names


def _assertion_weakened(removed: Sequence[str], added: Sequence[str]) -> str:
    """Describe how a diff loosened its assertions, or return ``""``.

    Three independent signals, each reported with the real text:

    1. fewer assertion statements than before (an assertion was removed);
    2. an exact comparison (``==``/``!=``/``is``) replaced by a bounded one;
    3. a new tolerance marker in the added lines.
    """

    problems: list[str] = []
    removed_asserts = _assertion_lines(removed)
    added_asserts = _assertion_lines(added)
    if len(added_asserts) < len(removed_asserts):
        problems.append(f"assertion count dropped from {len(removed_asserts)} to {len(added_asserts)}")
    removed_exact = sum(1 for line in removed_asserts if _EXACT_OPERATOR_RE.search(line))
    added_exact = sum(1 for line in added_asserts if _EXACT_OPERATOR_RE.search(line))
    if removed_exact > added_exact:
        problems.append(f"exact assertions dropped from {removed_exact} to {added_exact}")
    removed_loose = sum(1 for line in removed_asserts if _LOOSE_OPERATOR_RE.search(line))
    added_loose = sum(1 for line in added_asserts if _LOOSE_OPERATOR_RE.search(line))
    if added_loose > removed_loose:
        problems.append(f"bounded/containment assertions rose from {removed_loose} to {added_loose}")
    return "; ".join(problems)


def _surface_finding(changed: ChangedPath, category: str, policy: IntegrityPolicy) -> tuple[IntegrityFinding, ...]:
    """All findings for one changed evaluation-surface path."""

    path = changed.path
    findings: list[IntegrityFinding] = []

    def add(code: str, reason: str, severity: str = "blocker") -> None:
        findings.append(IntegrityFinding(path=path, code=code, reason=reason, category=category, severity=severity))

    if category == GATE_SELF_CATEGORY:
        name = path.rsplit("/", 1)[-1]
        what = SELF_JUDGE_FILES.get(name, "a file in the evidence-gate package")
        add("gate_self_modified", f"the evidence gate's own source was modified: {path} is {what}; a gate that edits itself cannot certify its own verdict")
        return tuple(findings)

    if category in BLOCKING_CATEGORIES:
        add(f"{category}_modified", f"{BLOCKING_CATEGORIES[category]}: {path} was {changed.change_type} in the same change that claims an improvement")

    if changed.deleted:
        if category in {"tests", "rsi_evaluator_surface"}:
            add("test_deleted", f"a test/evaluation file was deleted: {path}")
        elif category == "benchmark_cases":
            add("benchmark_case_deleted", f"a benchmark case was deleted: {path}")
        elif category == "metric_implementations":
            add("metric_implementation_deleted", f"the implementation of a measured metric was deleted: {path}")
        elif category == "baselines":
            add("baseline_deleted", f"a recorded baseline was deleted: {path}")
        return tuple(findings)

    skip_hits = _marker_hits(changed.added_lines, policy.skip_markers)
    if skip_hits:
        add("skip_marker_added", f"a skip/xfail marker was added to an evaluation surface ({', '.join(skip_hits)}): {path}")
    tolerance_hits = _marker_hits(changed.added_lines, policy.tolerance_markers)
    if tolerance_hits:
        add("tolerance_added", f"a tolerance/floating-point escape was added to an evaluation surface ({', '.join(tolerance_hits)}): {path}")
    removed_tests = _removed_test_names(changed.removed_lines)
    if removed_tests:
        add("test_case_removed", f"{len(removed_tests)} test case(s) were removed from {path}: {', '.join(removed_tests)}")
    weakened = _assertion_weakened(changed.removed_lines, changed.added_lines)
    if weakened and category in {"tests", "rsi_evaluator_surface", "metric_implementations"}:
        add("assertion_loosened", f"assertions were loosened in {path}: {weakened}")
    if not findings:
        findings.append(
            IntegrityFinding(
                path=path,
                code="evaluation_surface_modified",
                reason=f"an evaluation surface ({category}) was {changed.change_type} in the same change that claims an improvement: {path}",
                category=category,
                severity="suspect",
            )
        )
    return tuple(findings)


def _fingerprint_findings(policy: IntegrityPolicy, baseline: Mapping[str, str], current: Mapping[str, str]) -> list[IntegrityFinding]:
    """Compare two injected fingerprint sets over the evaluation surface."""

    findings: list[IntegrityFinding] = []
    baseline_paths = {str(key): str(value) for key, value in dict(baseline).items()}
    current_paths = {str(key): str(value) for key, value in dict(current).items()}
    for path in sorted(set(baseline_paths) | set(current_paths)):
        category = policy.classify(path)
        if category is None:
            continue
        before = baseline_paths.get(path)
        after = current_paths.get(path)
        if before is None:
            findings.append(
                IntegrityFinding(
                    path=path,
                    code="surface_file_unbaselined",
                    reason=f"evaluation-surface file {path} ({category}) is not present in the injected baseline fingerprint set",
                    category=category,
                    severity="suspect",
                )
            )
        elif after is None:
            findings.append(
                IntegrityFinding(
                    path=path,
                    code="surface_file_removed",
                    reason=f"evaluation-surface file {path} ({category}) is in the baseline fingerprint set but missing now (baseline {before})",
                    category=category,
                    severity="blocker",
                )
            )
        elif before != after:
            findings.append(
                IntegrityFinding(
                    path=path,
                    code="fingerprint_mismatch",
                    reason=f"evaluation-surface file {path} ({category}) changed on disk: baseline {before}, current {after}",
                    category=category,
                    severity="blocker",
                )
            )
    return findings


def check_evaluator_integrity(
    changed_paths: Sequence[ChangedPath | str] = (),
    *,
    policy: IntegrityPolicy | None = None,
    baseline_fingerprints: Mapping[str, str] | None = None,
    current_fingerprints: Mapping[str, str] | None = None,
) -> EvaluationIntegrityReport:
    """Check a proposal's touched paths for attempts to weaken the judge.

    Args:
        changed_paths: The proposal's touched paths (strings are accepted and
            treated as modifications with no diff lines).
        policy: Injected evaluation-surface policy; defaults to
            :func:`default_integrity_policy`.
        baseline_fingerprints: Optional ``{path: sha256}`` baseline set.
        current_fingerprints: Optional ``{path: sha256}`` current set. When both
            are supplied, every evaluation-surface path is compared and a
            mismatch/absence is reported.

    Returns:
        An :class:`~alpha.evolution.evidence.models.EvaluationIntegrityReport`
        whose status is ``clean``, ``suspect``, ``compromised`` or ``error``.
        ``clean`` requires that no evaluation surface was touched at all.
    """

    try:
        active_policy = policy if policy is not None else default_integrity_policy()
        if baseline_fingerprints is not None and not isinstance(baseline_fingerprints, Mapping):
            raise TypeError(f"baseline_fingerprints must be a mapping, got {type(baseline_fingerprints).__name__}")
        if current_fingerprints is not None and not isinstance(current_fingerprints, Mapping):
            raise TypeError(f"current_fingerprints must be a mapping, got {type(current_fingerprints).__name__}")
        findings: list[IntegrityFinding] = []
        checked: list[str] = []
        for raw in changed_paths:
            changed = ChangedPath.coerce(raw)
            category = active_policy.classify(changed.path)
            if category is None:
                continue
            checked.append(changed.path)
            findings.extend(_surface_finding(changed, category, active_policy))
        if baseline_fingerprints is not None and current_fingerprints is not None:
            for finding in _fingerprint_findings(active_policy, baseline_fingerprints, current_fingerprints):
                if finding.path not in checked:
                    checked.append(finding.path)
                findings.append(finding)
    except (TypeError, ValueError) as exc:
        return EvaluationIntegrityReport(status="error", findings=(), checked_paths=(), reason=f"evaluator-integrity check could not run: {type(exc).__name__}: {exc}")

    ordered = tuple(sorted(findings, key=lambda item: (item.path, item.code)))
    checked_paths = tuple(dict.fromkeys(checked))
    if not ordered:
        status = "clean"
        reason = "no evaluation surface was touched by this change"
    elif any(finding.severity == "blocker" for finding in ordered):
        status = "compromised"
        blockers = [finding for finding in ordered if finding.severity == "blocker"]
        reason = f"{len(blockers)} evaluation-integrity blocker(s) detected: " + "; ".join(f"{finding.path}: {finding.reason}" for finding in blockers)
    else:
        status = "suspect"
        reason = f"{len(ordered)} unexplained change(s) on an evaluation surface: " + "; ".join(f"{finding.path}: {finding.reason}" for finding in ordered)
    return EvaluationIntegrityReport(status=status, findings=ordered, checked_paths=checked_paths, reason=reason)
