"""Shared value types for the AVO decision layer.

This module is a **leaf**: it imports nothing from the rest of ``alpha.avo`` so
that the commit rule, the lineage chain, the scorer authority and the honesty
metrics can all share the same vocabulary without a circular import.

Three ideas live here, and they are the three that the rest of the package
exists to enforce.

1. **Correctness and invariants are evidence, not opinions.** A
   :class:`VerificationReceipt` and an :class:`InvariantEvidence` are only ever
   minted by the server, and each one is bound to the exact
   :func:`code_digest` of the source it was produced for. Evidence minted for a
   different revision is not evidence for this candidate.
2. **A rejection has a category.** :class:`RejectionCategory` is the coarse
   taxonomy (correctness / invariant / regression / abandoned / authority) and
   :class:`RejectionReason` is the fine-grained literal. The coarse category
   exists because "rejected" with no reason is not a record.
3. **Progress has a kind, not just a magnitude.** :func:`classify_change`
   distinguishes a structural discovery from a micro-refinement, because on a
   throughput curve those two are indistinguishable and they are not the same
   event.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "GENESIS_HASH",
    "ChangeKind",
    "InvariantEvidence",
    "RejectionCategory",
    "RejectionReason",
    "VerificationReceipt",
    "classify_change",
    "code_digest",
    "record_score",
]


#: The ``prev_hash`` of the first link in any append-only chain.
GENESIS_HASH = "0" * 64


def code_digest(source: str) -> str:
    """Stable sha256 of ``source``.

    Used to bind a receipt or an invariant report to one exact revision. Two
    revisions that differ by a single byte get different digests, which is what
    makes "the evidence was produced for this code" a checkable claim rather
    than a promise.
    """
    return hashlib.sha256(source.encode("utf-8", errors="surrogatepass")).hexdigest()


def record_score(record: Any) -> float:
    """The single comparable score for a :class:`~alpha.avo.lineage.VersionRecord`.

    **A candidate that fails correctness scores exactly ``0.0``.** This is a
    hard zero, not a weighted term: there is no arithmetic in this function or
    anywhere else in the decision path that can average a correctness failure
    into a pass. ``EvaluationVector.geometric_mean`` independently returns 0.0
    for an incorrect vector, so both routes agree.

    The score is the **geometric mean of the measured metric vector**, which is
    how AVO treats ``f``: ``f(x) = (f1(x), ..., fn(x))``, an n-dimensional score
    compared as one quantity. A candidate only beats another if it does not
    lose on any dimension -- a 2x gain on one metric and a 2x loss on another
    gives a ratio of 1.0, not a win.

    The weighted ``composite_score`` is the fallback for records carrying no
    usable vector, and it is also clamped, so it is only reached when a
    measurement exists at all.
    """
    if not getattr(record, "correctness", False):
        return 0.0
    vector = getattr(record, "vector", None)
    metrics = getattr(vector, "metrics", None)
    if metrics:
        geometric = float(vector.geometric_mean())
        if geometric > 0.0:
            return geometric
    compute = getattr(record, "compute_composite", None)
    if compute is None:  # pragma: no cover - defensive; every record has it
        return float(getattr(record, "composite_score", 0.0))
    return float(compute())


class RejectionCategory(StrEnum):
    """Coarse reason taxonomy. Every rejection lands in exactly one bucket.

    ``CORRECTNESS`` / ``INVARIANT`` / ``REGRESSION`` / ``ABANDONED`` are the four
    the search trajectory must be able to distinguish. ``AUTHORITY`` is the
    fifth and is the one that means "the model tried to change the thing that
    judges it".
    """

    CORRECTNESS = "correctness"
    INVARIANT = "invariant"
    REGRESSION = "regression"
    ABANDONED = "abandoned"
    AUTHORITY = "authority"


class RejectionReason(StrEnum):
    """Fine-grained rejection literal, each mapped to a :class:`RejectionCategory`."""

    #: Value preserved verbatim from the pre-existing ``rejection_reason``
    #: contract, which is asserted by a landed test. Renaming it would be a
    #: silent break of a recorded field.
    CORRECTNESS_FAILURE = "CORRECTNESS_FAILURE"
    CORRECTNESS_UNVERIFIED = "correctness_unverified"
    CORRECTNESS_RECEIPT_STALE = "correctness_receipt_for_other_revision"
    INVARIANT_VIOLATED = "invariant_violated"
    INVARIANT_EVIDENCE_MISSING = "invariant_evidence_missing"
    INVARIANT_EVIDENCE_STALE = "invariant_evidence_for_other_revision"
    SCORE_REGRESSION = "score_regression_against_best_committed"
    ABANDONED_BY_AGENT = "abandoned_by_agent"
    SCORER_INSTALL_REFUSED = "scorer_install_refused"
    GATE_MODIFICATION_REFUSED = "gate_modification_refused"
    GATE_FINGERPRINT_MISMATCH = "gate_fingerprint_mismatch"

    @property
    def category(self) -> RejectionCategory:
        if self in (
            RejectionReason.CORRECTNESS_FAILURE,
            RejectionReason.CORRECTNESS_UNVERIFIED,
            RejectionReason.CORRECTNESS_RECEIPT_STALE,
        ):
            return RejectionCategory.CORRECTNESS
        if self in (
            RejectionReason.INVARIANT_VIOLATED,
            RejectionReason.INVARIANT_EVIDENCE_MISSING,
            RejectionReason.INVARIANT_EVIDENCE_STALE,
        ):
            return RejectionCategory.INVARIANT
        if self in (RejectionReason.SCORE_REGRESSION, RejectionReason.GATE_FINGERPRINT_MISMATCH):
            return RejectionCategory.REGRESSION
        if self is RejectionReason.ABANDONED_BY_AGENT:
            return RejectionCategory.ABANDONED
        return RejectionCategory.AUTHORITY


class ChangeKind(StrEnum):
    """What kind of change a committed version is.

    AVO's trajectory analysis is explicit that progress arrives as a handful of
    architectural inflection points separated by plateaus, with the remaining
    versions contributing smaller but compounding refinement. Those two shapes
    look identical on a throughput curve, so the kind has to be recorded.
    """

    NO_OP = "no_op"
    REFINEMENT = "refinement"
    STRUCTURAL = "structural"


def _top_level_shape(source: str) -> tuple[tuple[str, str], ...] | None:
    """Return ``(node-type, qualified-name)`` for each top-level definition.

    ``None`` when the source does not parse, in which case the caller falls back
    to a text-shape comparison rather than pretending the file is structured.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    shape: list[tuple[str, str]] = []
    for node in tree.body:
        name = getattr(node, "name", None) or getattr(node, "target", None) or ""
        if isinstance(node, ast.Assign):
            shape.append(("Assign", str(getattr(node.targets[0], "id", ""))))
        elif name:
            shape.append((type(node).__name__, str(name)))
        else:
            shape.append((type(node).__name__, ""))
    return tuple(shape)


def classify_change(before: str, after: str) -> ChangeKind:
    """Classify a candidate against the revision it replaces.

    * identical text -> :attr:`ChangeKind.NO_OP`
    * same top-level definitions but different bodies/literals -> ``REFINEMENT``
    * definitions added, removed, renamed, or the file stops parsing into the
      same shape -> ``STRUCTURAL``

    This is a deliberately coarse structural classifier, not a semantic one. It
    answers the question the honesty report actually needs: was this a new idea
    or a tweak to the last one?
    """
    if before == after:
        return ChangeKind.NO_OP
    before_shape = _top_level_shape(before)
    after_shape = _top_level_shape(after)
    if before_shape is not None and after_shape is not None and before_shape != after_shape:
        return ChangeKind.STRUCTURAL
    return ChangeKind.REFINEMENT


@dataclass(frozen=True)
class VerificationReceipt:
    """A server-minted proof that the correctness oracle actually ran.

    The whole point of this type is that it cannot be produced by the model. It
    is minted only by code that executed the oracle and observed an exit code;
    it carries the digest of the revision it was produced for, so a receipt for
    revision A presented for revision B is detected rather than believed.
    """

    receipt_id: str
    target_digest: str
    oracle: str
    exit_code: int
    passed: bool
    authority: str = "server"
    minted_at: float = 0.0
    detail: str = ""

    @property
    def covers(self) -> str:
        """The digest this receipt is evidence for."""
        return self.target_digest

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "target_digest": self.target_digest,
            "oracle": self.oracle,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "authority": self.authority,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class InvariantEvidence:
    """A server-minted report from the invariant half of the gate.

    ``scope`` is recorded honestly rather than collapsed. When the gate could not
    run the invariant suite for a revision -- because the target is not
    parseable Python, for instance -- the scope says so and the honesty report
    counts those commits separately instead of pretending they were verified.
    """

    source: str
    target_digest: str
    scope: str = "measured"
    trials: int = 0
    regressions: int = 0
    consistency_score: float = 1.0
    detail: str = ""
    findings: tuple[str, ...] = field(default_factory=tuple)
    authority: str = "server"

    @property
    def verified(self) -> bool:
        return self.scope == "measured" and self.regressions == 0 and self.consistency_score >= 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target_digest": self.target_digest,
            "scope": self.scope,
            "trials": self.trials,
            "regressions": self.regressions,
            "consistency_score": self.consistency_score,
            "verified": self.verified,
            "detail": self.detail,
            "findings": list(self.findings),
            "authority": self.authority,
        }
