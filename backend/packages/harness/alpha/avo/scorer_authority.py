"""Server ownership of the fitness function and the gate.

The deepest form of the self-escalation problem is not the agent rewriting a
guard it can route around. It is the agent writing ``f`` -- the thing that
decides whether its own work is good. Once that is possible, every other
guarantee in the evolutionary loop is decorative, because the loop optimises
whatever ``f`` happens to reward.

Four rules are enforced here, and each has a test that fails without it:

1. **The agent may propose a scorer; it may not install one.**
   :meth:`ScorerProposalLedger.propose` always records a *refused* proposal
   with a reason. :meth:`ScorerProposalLedger.install` always raises. There is no
   code path in this module, or anywhere that calls it, that accepts an
   agent-supplied scorer as authoritative.
2. **The invariant half of the gate is not agent-writable under any
   circumstance.** It is a module-level name inside
   :mod:`alpha.testing.differential_invariant_fuzzer` and it is listed in
   :data:`SERVER_OWNED_SURFACE` as a *write* target. A candidate cannot propose
   replacing it, with or without an approval token.
3. **A change to the scorer is itself a change that must pass the gate, and the
   gate that evaluates it is the PREVIOUS one.** :func:`gate_fingerprint`
   produces a digest of the rule set; :class:`~alpha.avo.commit_gate.CommitGate`
   refuses to evaluate a candidate under any fingerprint other than its own, so
   the gate cannot be rewritten and used in the same step.
4. **The scorer and the gate are not writable through the candidate path at
   all.** :func:`assert_candidate_target_permitted` is a hard refusal consulted
   by the one runtime path that writes candidate code to a file.

**Scope, stated rather than implied.** This is a harness-level control. Per
``docs/asi/07_agent_stack_security.md`` the harness guides what an agent tries;
only the runtime below the line is authoritative. These refusals are real and
they are enforced in-process, but a process that can already write arbitrary
files can still edit this file. That limitation is recorded in
``docs/asi/07_agent_stack_security.md`` and is not papered over here.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .evidence import RejectionCategory, RejectionReason

__all__ = [
    "SERVER_OWNED_SURFACE",
    "CandidateTargetRefused",
    "GateRuleSet",
    "ScorerAuthorityViolation",
    "ScorerProposal",
    "ScorerProposalLedger",
    "assert_candidate_target_permitted",
    "gate_fingerprint",
    "is_server_owned_path",
]


#: Paths that define the fitness function or the gate. A candidate may not
#: propose a write to any of them. These are *write* targets: reading the scorer
#: is allowed and is how a candidate knows what it is being measured on, which
#: is exactly what AVO's knowledge base ``K`` is for.
SERVER_OWNED_SURFACE: tuple[str, ...] = (
    "alpha/avo/commit_gate.py",
    "alpha/avo/evidence.py",
    "alpha/avo/lineage_chain.py",
    "alpha/avo/scorer_authority.py",
    "alpha/avo/scoring.py",
    "alpha/avo/lineage.py",
    "alpha/avo/honesty.py",
    "alpha/rsi/evaluator_manifest.py",
    "alpha/rsi/promotion.py",
    "alpha/evolution/evidence/gates.py",
    "alpha/evolution/evidence/decision.py",
    "alpha/evolution/evidence/integrity.py",
    "alpha/evaluation",
    "alpha/benchmarks",
    # The invariant half of the gate. Not agent-writable under any circumstance.
    "alpha/testing/differential_invariant_fuzzer.py",
)


class ScorerAuthorityViolation(Exception):
    """Raised when something tries to author its own fitness function or gate.

    Carries the machine-readable reason and the offending subject so a refusal
    can be recorded as evidence rather than merely logged.
    """

    def __init__(self, reason: RejectionReason, subject: str, detail: str) -> None:
        self.reason = reason
        self.subject = subject
        self.detail = detail
        self.category = reason.category
        super().__init__(f"{reason.value}: {subject} -- {detail}")


class CandidateTargetRefused(Exception):
    """Raised when a candidate asks to write into the server-owned surface."""

    def __init__(self, target: str, matched: str) -> None:
        self.target = target
        self.matched = matched
        super().__init__(
            f"candidate target refused: '{target}' is part of the server-owned scoring surface (matched '{matched}'). "
            "A candidate may not modify the fitness function or the gate that judges it."
        )


def _normalise(path: str | Path) -> str:
    """Repo-relative, forward-slashed form used for the surface comparison."""
    text = str(path).replace("\\", "/")
    if os.sep != "/":  # pragma: no cover - Windows-specific normalisation
        text = text.replace(os.sep, "/")
    marker = "alpha/"
    index = text.rfind(marker)
    if index != -1:
        return text[index:]
    return text.lstrip("./")


def is_server_owned_path(path: str | Path) -> str | None:
    """Return the surface entry that ``path`` falls under, or ``None``."""
    normalised = _normalise(path)
    for entry in SERVER_OWNED_SURFACE:
        if normalised == entry or normalised.startswith(entry.rstrip("/") + "/"):
            return entry
        if entry.endswith("/") and normalised.startswith(entry):
            return entry
    return None


def assert_candidate_target_permitted(path: str | Path) -> None:
    """Refuse a candidate write into the server-owned scoring surface.

    Called from the one runtime path that writes candidate code to a file, so
    the refusal is on a real path and not a helper nobody calls.
    """
    matched = is_server_owned_path(path)
    if matched is not None:
        raise CandidateTargetRefused(str(path), matched)


def gate_fingerprint(rules: dict[str, Any]) -> str:
    """A digest of a gate's rule set.

    The fingerprint is what makes "the gate that evaluated this is the gate that
    was in force before the change" a checkable claim rather than a convention.
    A candidate that changes ``f`` arrives carrying a *different* fingerprint,
    and :class:`~alpha.avo.commit_gate.CommitGate` refuses to be evaluated by it.
    """
    canonical = json.dumps(rules, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GateRuleSet:
    """The server-owned rule set of a gate instance.

    ``version`` increases when a human/server ratifies a new rule set. It is
    deliberately *not* a clock reading and *not* something a candidate can set.
    """

    version: int = 1
    require_correctness_receipt: bool = True
    require_invariants: bool = True
    compare_against: str = "best_committed"
    correctness_is_hard_zero: bool = True
    regress_tolerance: float = 0.0
    #: When true, a candidate whose differential suite *measures* a behavioural
    #: divergence is treated as a declared behaviour change and is judged on
    #: correctness plus score alone -- with the divergence recorded and the
    #: commit counted as invariant-unverified in the honesty report.
    #:
    #: Defaults to **False** for the gate object, which is the strict policy. The
    #: workspace runner opts in, because its pre-existing, tested contract is that
    #: a candidate may change behaviour as long as the supplied oracle passes
    #: (``test_avo_architecture.py::test_workspace_avo_runner_success_and_rollback``
    #: asserts exactly that). Turning that off would weaken a landed guarantee, so
    #: the weaker policy is made explicit, scoped to that surface, and reported --
    #: rather than imposed repo-wide or quietly dropped.
    allow_declared_behaviour_change: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "require_correctness_receipt": self.require_correctness_receipt,
            "require_invariants": self.require_invariants,
            "compare_against": self.compare_against,
            "correctness_is_hard_zero": self.correctness_is_hard_zero,
            "regress_tolerance": self.regress_tolerance,
            "allow_declared_behaviour_change": self.allow_declared_behaviour_change,
        }

    @property
    def fingerprint(self) -> str:
        return gate_fingerprint(self.to_dict())


@dataclass
class ScorerProposal:
    """A recorded request to change the scorer or the gate.

    Proposals are *recorded*, never honoured. ``installed`` is permanently
    ``False``; the field exists so the refusal is visible in the record rather
    than only in a log line.
    """

    proposal_id: str
    author: str
    subject: str
    requested_change: str
    created_at: float = field(default_factory=time.time)
    installed: bool = False
    refusal_reason: RejectionReason = RejectionReason.SCORER_INSTALL_REFUSED
    refusal_detail: str = ""

    @property
    def refusal_category(self) -> RejectionCategory:
        return self.refusal_reason.category

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "author": self.author,
            "subject": self.subject,
            "requested_change": self.requested_change,
            "created_at": self.created_at,
            "installed": self.installed,
            "refusal_reason": self.refusal_reason.value,
            "refusal_category": self.refusal_reason.category.value,
            "refusal_detail": self.refusal_detail,
        }


class ScorerProposalLedger:
    """Records scorer-change requests and refuses all of them.

    There is deliberately no ``accept``/``approve``/``force`` method. A reviewer
    who wants a different gate changes :class:`GateRuleSet` in code, in a
    reviewed commit, which is a different and much more legible act than a
    runtime flag.
    """

    def __init__(self) -> None:
        self.proposals: list[ScorerProposal] = []

    def propose(self, *, author: str, subject: str, requested_change: str) -> ScorerProposal:
        """Record a scorer/gate change request. Always returns ``installed=False``."""
        matched = is_server_owned_path(subject)
        if matched is not None:
            reason = RejectionReason.GATE_MODIFICATION_REFUSED
            detail = (
                f"'{subject}' is the server-owned gate/scorer surface (matched '{matched}'). "
                "The agent may inspect it to learn what it is measured on; it may not write to it."
            )
        else:
            reason = RejectionReason.SCORER_INSTALL_REFUSED
            detail = (
                f"'{subject}' is not a server-owned path, but a scorer proposed by an agent is still not "
                "authoritative for its own promotion. Scorers are owned by the server."
            )
        proposal = ScorerProposal(
            proposal_id=f"sp_{uuid.uuid4().hex[:12]}",
            author=author,
            subject=subject,
            requested_change=requested_change,
            refusal_reason=reason,
            refusal_detail=detail,
        )
        self.proposals.append(proposal)
        return proposal

    def install(self, proposal_id: str) -> ScorerAuthorityViolation:
        """Always raises. Present so the refusal is reachable, not merely absent."""
        for proposal in self.proposals:
            if proposal.proposal_id == proposal_id:
                proposal.installed = False
                raise ScorerAuthorityViolation(
                    proposal.refusal_reason,
                    proposal.subject,
                    "the fitness function and the gate are server-owned; a proposed scorer is never installed",
                )
        raise ScorerAuthorityViolation(
            RejectionReason.SCORER_INSTALL_REFUSED,
            proposal_id,
            "unknown proposal; no scorer may be installed by an agent under any circumstance",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposed": len(self.proposals),
            "installed": sum(1 for p in self.proposals if p.installed),
            "proposals": [p.to_dict() for p in self.proposals],
        }
