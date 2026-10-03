"""Claim ledger: every consequential belief, and what is behind it.

## Why this is the centre of the package

The obvious fix for an agent that invents things is to make it search more. The
trajectory-audit literature says that is the wrong lever. Across 2,790
deep-research trajectories, retrieval spans carry a ~2.9% error rate while
**decision** spans carry 60.5% and **finalization** spans 51.8% — and 36.9% of
*successful* trajectories contain at least one error span, meaning the agent
recovered or got lucky. Retrieval is not where it goes wrong. Committing to,
verifying, and aggregating retrieved material is.

So the unit this module tracks is not a document or a search result. It is a
**claim**: a belief the agent is currently standing on. Every claim carries its
support, its evidence, and the steps that leaned on it.

## Three properties the design turns on

**Blast radius, not a boolean.** A claim nobody used is a note. A claim three
later steps depend on is a load-bearing premise, and those are the ones that
turn a small early mistake into a trajectory-level breakdown. ``used_by`` is
therefore part of the claim's state, and promotion to ``consequential`` is
automatic when a step consumes a claim rather than something the agent has to
opt into.

**Contradiction is detected by structure, not by a model.** Two claims whose
normalised text agrees except on polarity or a numeric value are a
``CONFLICTING`` pair. Without that, contradictory evidence produces a synthesised
hybrid that appears in no source — the failure mode that looks most confident.

**Time is a step counter.** Nothing here reads the clock. A ledger replayed from
the same spans produces the same report, which is what lets it be a test fixture
and an audit artifact rather than a mood.

## What it does not do

It does not decide truth, score probability, or replace
:mod:`alpha.epistemics`. That engine updates a Bayesian posterior over a claim;
this one records what the trajectory actually cited and who leaned on it. The two
answer different questions and folding them together would mean either losing
the audit trail or pretending a posterior is evidence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from alpha.grounding.models import BLOCKING_SUPPORT, ClaimKind, SupportStatus, TrustTier

__all__ = [
    "Claim",
    "ClaimLedger",
    "EvidenceRef",
    "audit_claims",
    "normalize_claim_text",
]

#: Whitespace and punctuation collapsed before comparison. Quotes and
#: casing are *not* normalised away: ``'ok'`` and `"ok"` are the same answer, but
#: ``'ok'`` and ``not ok`` are the disagreement this module exists to catch.
_NORMALIZE_RE = re.compile(r"[\s\-_/\\.,:;!?]+")

_NEGATIONS = ("not", "no", "never", "cannot", "can't", "won't", "doesn't", "isn't", "false", "disabled")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def normalize_claim_text(text: str) -> str:
    """Collapse a claim to the form contradictions are detected on."""
    return _NORMALIZE_RE.sub(" ", text.strip().lower()).strip()


@dataclass(frozen=True)
class EvidenceRef:
    """One thing the agent observed.

    ``ref`` is a locator the caller can re-check — a span id, ``path:line``, a
    tool-call id, a URL. Free text is not acceptable here: a claim supported by
    a description of a source rather than the source is the same defect as a
    claim supported by nothing, and it is harder to notice.
    """

    ref: str
    kind: str = "observation"
    tier: TrustTier = TrustTier.TOOL_OBSERVED
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.ref.strip():
            raise ValueError("evidence ref must be a non-empty locator, not prose")
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("evidence kind must be a non-empty string")

    @property
    def authority(self) -> float:
        from alpha.grounding.models import TRUST_WEIGHT

        return TRUST_WEIGHT[self.tier]


@dataclass
class Claim:
    """A tracked belief and its standing."""

    claim_id: str
    text: str
    kind: ClaimKind
    support: SupportStatus
    #: Logical step counter, never a wall clock.
    introduced_at: int
    evidence: tuple[EvidenceRef, ...] = ()
    #: Steps that leaned on this claim. Populated by :meth:`ClaimLedger.record_use`.
    used_by: tuple[int, ...] = ()
    #: True when something irreversible or user-visible depends on it.
    consequential: bool = False
    #: Where the claim came from, so a claim the agent invented about itself is
    #: distinguishable from one the user asserted.
    origin: str = ""
    updated_at: int = 0

    @property
    def normalized(self) -> str:
        return normalize_claim_text(self.text)

    @property
    def authorized(self) -> bool:
        """Whether this claim may stand behind a decision."""
        return self.support not in BLOCKING_SUPPORT

    @property
    def blast_radius(self) -> int:
        """How many distinct steps depend on this claim."""
        return len(set(self.used_by))

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "kind": self.kind.value,
            "support": self.support.value,
            "introduced_at": self.introduced_at,
            "updated_at": self.updated_at or self.introduced_at,
            "evidence": [{"ref": e.ref, "kind": e.kind, "tier": e.tier.value, "detail": e.detail} for e in self.evidence],
            "used_by": list(self.used_by),
            "blast_radius": self.blast_radius,
            "consequential": self.consequential,
            "origin": self.origin,
            "authorized": self.authorized,
        }


def _polarity(normalized: str) -> bool:
    """True when the claim asserts something; False when it denies something."""
    tokens = normalized.split()
    return not any(token in _NEGATIONS for token in tokens[:3])


def _numbers(normalized: str) -> tuple[str, ...]:
    return tuple(_NUMBER_RE.findall(normalized))


@dataclass
class ClaimLedger:
    """The tracked claims for one scope, plus the contradictions among them.

    Scope is a constructor argument rather than a field because a ledger is meant
    to be owned per thread or per run; a single process-wide ledger would make two
    unrelated threads argue about the same claim.
    """

    scope: str = "default"
    #: How many claims to retain. Bounded because a ledger that grows forever
    #: becomes a second memory store with none of memory's eviction semantics.
    max_claims: int = 500
    _claims: dict[str, Claim] = field(default_factory=dict, init=False, repr=False)
    _by_normalized: dict[str, set[str]] = field(default_factory=dict, init=False, repr=False)
    _conflicts: list[tuple[str, str]] = field(default_factory=list, init=False, repr=False)

    # -- registration ----------------------------------------------------

    def register(
        self,
        text: str,
        *,
        kind: ClaimKind = ClaimKind.FACT,
        evidence: Iterable[EvidenceRef] = (),
        step: int = 0,
        origin: str = "",
        consequential: bool | None = None,
    ) -> Claim:
        """Register a claim, or return the existing one for the same text.

        Registering the same text twice returns the same claim id rather than
        forking it. A fork would be indistinguishable from a second belief about
        the same thing, and would let one of the two be "fixed" while the other
        kept doing damage.
        """
        if not text.strip():
            raise ValueError("claim text must be non-empty")
        normalized = normalize_claim_text(text)
        existing = self._by_normalized.get(normalized)
        if existing:
            claim_id = sorted(existing)[0]
            return self.attach_evidence(claim_id, evidence, step=step)

        claim_id = f"c{len(self._claims) + 1}"
        support = SupportStatus.DIRECT if tuple(evidence) else SupportStatus.MISSING
        # A decision is consequential by definition: something irreversible is
        # being stood behind. An assumption is never auto-consequential because
        # the agent may be carrying it as an explicit hedge, which is the
        # healthy case.
        if consequential is None:
            consequential = kind is ClaimKind.DECISION
        claim = Claim(
            claim_id=claim_id,
            text=text.strip(),
            kind=kind,
            support=support,
            introduced_at=step,
            evidence=tuple(evidence),
            consequential=consequential,
            origin=origin,
            updated_at=step,
        )
        self._claims[claim_id] = claim
        self._by_normalized.setdefault(normalized, set()).add(claim_id)
        self._detect_conflicts(claim)
        self._evict()
        return claim

    def _evict(self) -> None:
        """Drop the oldest non-consequential claims past the cap.

        Consequential claims are never evicted: they are the ones a later step is
        still standing on, and forgetting one is how a run resumes onto a premise
        nobody re-checked. When everything left is consequential the ledger stops
        evicting rather than deleting a live premise — an over-full ledger is a
        visible condition, a silently forgotten claim is not.
        """
        overflow = len(self._claims) - self.max_claims
        if overflow <= 0:
            return
        for claim_id in sorted(self._claims, key=lambda cid: self._claims[cid].introduced_at):
            if overflow <= 0:
                break
            if self._claims[claim_id].consequential:
                continue
            self._forget(claim_id)
            overflow -= 1

    def _forget(self, claim_id: str) -> None:
        claim = self._claims.pop(claim_id, None)
        if claim is None:
            return
        siblings = self._by_normalized.get(claim.normalized)
        if siblings:
            siblings.discard(claim_id)
            if not siblings:
                self._by_normalized.pop(claim.normalized, None)
        self._conflicts = [pair for pair in self._conflicts if claim_id not in pair]

    # -- evidence --------------------------------------------------------

    def attach_evidence(self, claim_id: str, evidence: Iterable[EvidenceRef], *, step: int = 0) -> Claim:
        """Add evidence and re-derive support.

        Support is re-derived rather than set, because a caller may attach a
        citation that does not actually establish the claim. ``DIRECT`` requires
        at least one non-model tier: a model restating its own belief is not
        evidence for it, and letting that count is how a hallucination cites
        itself.
        """
        claim = self.require(claim_id)
        merged = list(claim.evidence)
        seen = {(e.ref, e.kind) for e in merged}
        for item in evidence:
            if (item.ref, item.kind) not in seen:
                merged.append(item)
                seen.add((item.ref, item.kind))
        derived = self._derive_support(tuple(merged), claim.text)
        updated = replace(claim, evidence=tuple(merged), support=derived, updated_at=step)
        self._claims[claim_id] = updated
        self._detect_conflicts(updated)
        return updated

    @staticmethod
    def _derive_support(evidence: Sequence[EvidenceRef], text: str) -> SupportStatus:
        """Support implied by an evidence set alone.

        ``DIRECT`` requires a citation from outside the model. That single rule
        is what stops "I checked and it is fine" from being recorded as a check.
        """
        if not evidence:
            return SupportStatus.MISSING
        external = [e for e in evidence if e.tier is not TrustTier.MODEL_DERIVED]
        if not external:
            return SupportStatus.WEAK
        strong = [e for e in external if e.authority >= TRUST_WEIGHT_STRONG]
        if not strong:
            return SupportStatus.WEAK
        normalized = normalize_claim_text(text)
        # Evidence that points at the claim's own words is a restatement.
        if any(normalized and normalized in normalize_claim_text(e.ref) for e in external):
            return SupportStatus.WEAK
        return SupportStatus.DIRECT

    def mark_conflicting(self, claim_id: str, other_claim_id: str, *, step: int = 0) -> Claim:
        """Record that two claims cannot both be true.

        Sets both to ``CONFLICTING`` rather than downgrading one. Picking a
        winner here would be an arbitration this layer has no authority to
        perform; resolving it is the agent's job, with the evidence shown.
        """
        first = self.require(claim_id)
        second = self.require(other_claim_id)
        pair = (claim_id, other_claim_id) if claim_id <= other_claim_id else (other_claim_id, claim_id)
        if pair not in self._conflicts:
            self._conflicts.append(pair)
        for target in (first, second):
            updated = replace(target, support=SupportStatus.CONFLICTING, updated_at=step)
            self._claims[target.claim_id] = updated
        return self._claims[claim_id]

    def _detect_conflicts(self, claim: Claim) -> None:
        """Flag a claim that denies an existing one, or contradicts its numbers."""
        normalized = claim.normalized
        polarity = _polarity(normalized)
        numbers = _numbers(normalized)
        for other in self._claims.values():
            if other.claim_id == claim.claim_id or other.normalized == normalized:
                continue
            if _polarity(other.normalized) == polarity:
                continue
            if _numbers(other.normalized) == numbers:
                self.mark_conflicting(claim.claim_id, other.claim_id)

    # -- use -------------------------------------------------------------

    def record_use(self, claim_id: str, step: int) -> Claim:
        """Note that *step* leaned on this claim, promoting it to consequential.

        Promotion is automatic here and nowhere else in the package. It is the
        whole reason ``used_by`` exists: the agent should not have to remember
        to declare a premise load-bearing.
        """
        claim = self.require(claim_id)
        if step in claim.used_by:
            return claim
        updated = replace(claim, used_by=claim.used_by + (step,), consequential=True, updated_at=step)
        self._claims[claim_id] = updated
        return updated

    def record_uses(self, claim_ids: Iterable[str], step: int) -> tuple[Claim, ...]:
        return tuple(self.record_use(cid, step) for cid in claim_ids)

    # -- reads -----------------------------------------------------------

    def require(self, claim_id: str) -> Claim:
        claim = self._claims.get(claim_id)
        if claim is None:
            raise KeyError(f"unknown claim {claim_id!r}")
        return claim

    def get(self, claim_id: str) -> Claim | None:
        return self._claims.get(claim_id)

    def __len__(self) -> int:
        return len(self._claims)

    def all(self) -> tuple[Claim, ...]:
        return tuple(sorted(self._claims.values(), key=lambda c: c.introduced_at))

    def blocking(self) -> tuple[Claim, ...]:
        """Consequential claims whose support forbids them from being stood on."""
        return tuple(c for c in self.all() if c.consequential and c.support in BLOCKING_SUPPORT)

    def conflicts(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._conflicts)

    def unsupported(self) -> tuple[Claim, ...]:
        return tuple(c for c in self.all() if not c.authorized)

    def to_report(self) -> dict[str, Any]:
        """The full audit, safe to persist in run metadata or return over HTTP."""
        blocking = self.blocking()
        return {
            "scope": self.scope,
            "total": len(self._claims),
            "consequential": sum(1 for c in self._claims.values() if c.consequential),
            "unsupported": sum(1 for c in self._claims.values() if not c.authorized),
            "conflicts": [{"a": a, "b": b} for a, b in self._conflicts],
            "blocking": [c.claim_id for c in blocking],
            "claims": [c.to_dict() for c in self.all()],
        }

    def render_for_prompt(self, *, limit: int = 12) -> str:
        """A compact ledger block for the agent's context.

        Blocking claims come first and are never dropped by the limit: a truncated
        brief that silently omits the unsupported premise is worse than no brief,
        because it reads as clearance. Non-blocking claims are budgeted instead.
        """
        blocking = self.blocking()
        if not blocking:
            return ""
        lines = ["<grounding_ledger>"]
        for claim in blocking:
            refs = ", ".join(f"{e.ref} ({e.tier.value})" for e in claim.evidence[:3]) or "no evidence cited"
            lines.append(f"- {claim.claim_id} [{claim.support.value}] {claim.text} — evidence: {refs}; depended on by {sorted(set(claim.used_by))}")
        remainder = [c for c in self.all() if c.authorized]
        for claim in remainder[: max(0, limit - len(blocking))]:
            refs = ", ".join(e.ref for e in claim.evidence[:2]) or "none"
            lines.append(f"- {claim.claim_id} [supported] {claim.text} — {refs}")
        lines.append("</grounding_ledger>")
        return "\n".join(lines)


#: Minimum authority for evidence to be treated as directly supporting a claim.
#: Matches ``TRUST_WEIGHT[TrustTier.USER_STATED]``.
TRUST_WEIGHT_STRONG = 0.75


def audit_claims(
    ledger: ClaimLedger,
    *,
    require_evidence_for: Sequence[ClaimKind] = (ClaimKind.FACT, ClaimKind.DECISION),
) -> dict[str, Any]:
    """Audit a ledger and return a verdict plus the reasons it failed.

    The split between *failed the audit* and *passed the audit* is kept explicit:
    a run whose claims were never audited is not a passing audit, so
    :attr:`AuditVerdict.audited` exists and defaults to the honest answer.

    ``ASSUMPTION`` is excluded from the evidence requirement on purpose. An
    assumption the agent states in order to keep going is a legitimate hedge; a
    fact it asserts without a source is the defect.
    """
    from alpha.grounding.gates import GateLayer, GateResult

    required = tuple(require_evidence_for)
    results: list[GateResult] = []
    for claim in ledger.all():
        if claim.kind in required and claim.support is not SupportStatus.DIRECT:
            results.append(
                GateResult(
                    gate=f"claim_support:{claim.claim_id}",
                    layer=GateLayer.DETERMINISTIC,
                    blocked=True,
                    code="unsupported_claim",
                    reason=f"claim {claim.claim_id} is {claim.support.value} but is {claim.kind.value}",
                    remediation="cite an observed source, or downgrade the claim to an assumption and say so",
                    detail={"claim_id": claim.claim_id, "kind": claim.kind.value, "support": claim.support.value},
                )
            )
    for left, right in ledger.conflicts():
        results.append(
            GateResult(
                gate=f"claim_conflict:{left}:{right}",
                layer=GateLayer.DETERMINISTIC,
                blocked=True,
                code="contradictory_claims",
                reason=f"claims {left} and {right} cannot both hold",
                remediation="resolve against cited evidence before acting on either",
                detail={"claims": [left, right]},
            )
        )
    return {
        "audited": True,
        "scope": ledger.scope,
        "passed": not results,
        "claim_count": len(ledger),
        "blocking_claim_ids": [c.claim_id for c in ledger.blocking()],
        "results": [r.to_dict() for r in results],
    }


def _mapping_evidence(items: Iterable[Mapping[str, Any]]) -> tuple[EvidenceRef, ...]:
    """Build evidence from plain mappings, for callers holding JSON."""
    return tuple(
        EvidenceRef(
            ref=str(item["ref"]),
            kind=str(item.get("kind", "observation")),
            tier=TrustTier(item.get("tier", TrustTier.TOOL_OBSERVED.value)),
            detail=str(item.get("detail", "")),
        )
        for item in items
        if isinstance(item, Mapping) and item.get("ref")
    )
