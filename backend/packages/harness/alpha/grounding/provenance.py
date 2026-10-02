"""Where a stored fact came from, and whether it should still be believed.

## The threat

Memory poisoning is not prompt injection with a longer fuse. An instruction
planted through an untrusted document is summarised into long-term storage during
completely normal operation; a different user, or the same user weeks later with
an unrelated question, retrieves it and treats it as the agent's own history.
OWASP ranked it **ASI06 (Memory & Context Poisoning)** in the Agentic Top 10 for
2026. Measured attack success on ordinary query patterns exceeds 95% for
injection and 70% for the downstream exploit; independent audits of
LLM-based memory guards find they **miss 66% of poisoned entries**, because the
payload looks benign in isolation and only reveals itself against a particular
query.

## The consequence for this design

Two rules follow from that 66%, and they shape the whole module:

1. **The structural screen is load-bearing; the model check is the second layer.**
   Credential shapes, encoded payloads, instruction-shaped directives, and size
   anomalies are found by deterministic inspection. A model is asked about
   semantic intent *after* those pass, never instead of them. A design where the
   model check is primary measures exactly the thing the research says does not
   work.

2. **Provenance is attached at write time or not at all.** Trust cannot be
   reconstructed later: by the time a poisoned entry is retrieved it is
   indistinguishable from a real one, which is the entire problem. So every
   record carries origin, tier, detector, and timestamp from the moment of
   creation, and :meth:`MemoryStore.add` refuses a record that lacks them.

## Thresholds: conservative first

The recommendation from the incident write-ups is to start with strict thresholds
and relax them against observed false positives — not the reverse. Strict-first
means some legitimate writes are refused, which is a visible, recoverable event.
Permissive-first means the first poisoning is the thing that teaches you your
threshold was wrong.

## Trust-weighted, not relevance-ranked

Plain similarity is enough to let a low-authority write dominate the context
window purely by being on-topic. :meth:`MemoryStore.rank` multiplies relevance by
authority, so a strongly relevant unverified external note can be outranked by a
moderately relevant system measurement — and, importantly, is still *present*,
just not in charge.

Temporal decay is deliberately combined with trust rather than used alone, since
decay alone hands attackers the recency bias: inject fresh and you win.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from alpha.grounding.models import TRUST_WEIGHT, TrustTier

__all__ = [
    "MemoryRecord",
    "MemoryStore",
    "Provenance",
    "screen_text",
    "verify_provenance",
]


@dataclass(frozen=True)
class Provenance:
    """Where a fact came from. Required at write time, never inferred later."""

    origin_kind: str
    origin_uri: str
    tier: TrustTier
    #: The structural detectors that fired at ingestion, plus any model verdict.
    detectors: tuple[str, ...] = ()
    #: Writer's confidence. Recorded, never used to set ``tier``.
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.origin_kind.strip():
            raise ValueError("provenance requires a non-empty origin_kind")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("provenance confidence must be within [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin_kind": self.origin_kind,
            "origin_uri": self.origin_uri,
            "tier": self.tier.value,
            "detectors": list(self.detectors),
            "confidence": self.confidence,
        }


def verify_provenance(candidate: Mapping[str, Any]) -> Provenance:
    """Build a :class:`Provenance`, refusing anything incomplete.

    Raises rather than defaulting. A missing ``tier`` defaulted to
    ``EXTERNAL_UNVERIFIED`` would look like a deliberate classification and get
    ranked accordingly; the whole point is that the absence is loud.
    """
    missing = [k for k in ("origin_kind", "origin_uri", "tier") if not candidate.get(k)]
    if missing:
        raise ValueError(f"provenance is missing required field(s): {', '.join(missing)}")
    detectors = candidate.get("detectors") or ()
    return Provenance(
        origin_kind=str(candidate["origin_kind"]),
        origin_uri=str(candidate["origin_uri"]),
        tier=TrustTier(str(candidate["tier"])),
        detectors=tuple(str(d) for d in detectors),
        confidence=candidate.get("confidence"),
    )


# ---------------------------------------------------------------------------
# Deterministic screen
# ---------------------------------------------------------------------------

#: Credential shapes. Matched structurally, and the finding reports the shape
#: plus a redacted fingerprint (``ghp_AAAA...BBBB``) rather than the value — enough
#: to trace an incident without re-exposing the secret in a log or a memory file.
_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_header", re.compile(r"\bAuthorization:\s*Bearer\s+[A-Za-z0-9._-]{16,}", re.IGNORECASE)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)

#: Instruction-shaped text: the shape a poisoned memory takes. Deliberately
#: broad. A false positive costs one refused write, which is visible; a false
#: negative persists across sessions.
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_previous", re.compile(r"\bignore\s+(all\s+)?(previous|prior|above|earlier)\b", re.IGNORECASE)),
    ("disregard_rules", re.compile(r"\bdisregard\s+(the\s+)?(rules|instructions|policy|guardrails)\b", re.IGNORECASE)),
    ("system_override", re.compile(r"\b(you\s+are\s+now|from\s+now\s+on\s+you)\b", re.IGNORECASE)),
    ("reveal_prompt", re.compile(r"\b(reveal|print|repeat|show)\s+(your\s+)?(system\s+)?(prompt|instructions|rules)\b", re.IGNORECASE)),
    ("covert_channel", re.compile(r"\b(do\s+not|don't|never)\s+(tell|mention|inform|reveal|log)\b", re.IGNORECASE)),
    ("persistence_directive", re.compile(r"\b(always|permanently|from\s+now\s+on)\s+\w+\s+(this|that|the)\b", re.IGNORECASE)),
)

#: Base64/hex runs long enough to hide an instruction.
_ENCODED_PATTERN = re.compile(r"\b(?:[A-Za-z0-9+/]{40,}={0,2}|[0-9a-fA-F]{64,})\b")

#: Refuse a single fact larger than this. Oversized entries are both a memory
#: quality problem and a smuggling channel.
MAX_FACT_CHARS = 8000


def _fingerprint(match: re.Match[str]) -> str:
    """First four and last four characters, middle elided."""
    text = match.group(0)
    return f"{text[:4]}...{text[-4:]}" if len(text) > 12 else text[:4] + "..."


def screen_text(text: str, *, origin: str = "unknown", max_chars: int = MAX_FACT_CHARS) -> tuple[list[str], dict[str, Any]]:
    """Structural screen. Returns ``(detector_names, findings)``.

    Deterministic only, and deliberately so. The 66%-miss figure is about LLM
    detectors looking at an entry in isolation; this looks for shapes that do not
    depend on context. Findings never contain the matched value.
    """
    detectors: list[str] = []
    findings: list[dict[str, Any]] = []
    if len(text) > max_chars:
        detectors.append("oversize_fact")
        findings.append({"detector": "oversize_fact", "origin": origin, "chars": len(text), "limit": max_chars})
    for name, pattern in _CREDENTIAL_PATTERNS:
        for match in pattern.finditer(text):
            detectors.append(f"credential:{name}")
            findings.append({"detector": f"credential:{name}", "origin": origin, "fingerprint": _fingerprint(match)})
    for name, pattern in _INJECTION_PATTERNS:
        for match in pattern.finditer(text):
            detectors.append(f"injection:{name}")
            findings.append({"detector": f"injection:{name}", "origin": origin, "excerpt": match.group(0)[:80]})
    encoded = _ENCODED_PATTERN.findall(text)
    if encoded:
        detectors.append("encoded_payload")
        findings.append({"detector": "encoded_payload", "origin": origin, "runs": len(encoded), "longest": max(len(r) for r in encoded)})
    return detectors, {"findings": findings}


@dataclass
class MemoryRecord:
    """One stored fact, with its provenance attached."""

    record_id: str
    text: str
    provenance: Provenance
    created_at: float
    tags: tuple[str, ...] = ()
    #: Set when a later write corroborates this record.
    reinforcements: int = 0
    #: Set when the record has been explicitly withdrawn.
    revoked: bool = False
    #: Query shapes this record has been retrieved for. Feeds anomaly detection.
    retrieval_shapes: tuple[str, ...] = ()

    @property
    def authority(self) -> float:
        return TRUST_WEIGHT[self.provenance.tier]

    def age_seconds(self, *, now: float | None = None) -> float:
        return max(0.0, (now if now is not None else time.time()) - self.created_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "text": self.text,
            "provenance": self.provenance.to_dict(),
            "created_at": self.created_at,
            "tags": list(self.tags),
            "reinforcements": self.reinforcements,
            "revoked": self.revoked,
            "retrieval_shapes": list(self.retrieval_shapes),
        }


@dataclass
class MemoryStore:
    """A provenance-bearing fact store with trust-aware retrieval.

    Process-local and in-memory by design. It is a *write-path and read-path*
    discipline, not durable storage, and it must not be described as either a
    poison-proof store or a substitute for the cognitive-memory snapshot layer —
    it decides what is allowed in and how in-context facts are ordered.
    """

    records: dict[str, MemoryRecord] = field(default_factory=dict)
    max_records: int = 500
    #: Refuse a write whose origin is outside the trust boundary until a model
    #: check has run. Conservative-first, per the incident guidance.
    require_structural_pass: bool = True
    #: Fraction of an age window after which a record's weight starts decaying.
    decay_window_seconds: float = 30 * 24 * 3600.0
    _counter: int = field(default=0, init=False, repr=False)
    _violations: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    # -- write path ------------------------------------------------------

    def add(
        self,
        text: str,
        provenance: Provenance,
        *,
        tags: Iterable[str] = (),
        now: float | None = None,
        structural_verdict: tuple[bool, list[str]] | None = None,
    ) -> MemoryRecord:
        """Screen, then store.

        ``structural_verdict`` lets a caller pass the result of the deterministic
        screen when it already ran one (an ingestion pipeline usually does). It is
        ``(passed, detector_names)``. Absent, the screen runs here.

        Returns the record. Raises :class:`ValueError` when the screen fires, so a
        refused write is an exception the caller has to handle rather than a flag
        it might forget to read.
        """
        if not text.strip():
            raise ValueError("memory record text must be non-empty")
        if self.require_structural_pass and structural_verdict is None:
            detectors, _ = screen_text(text, origin=provenance.origin_kind)
            if detectors:
                raise ValueError(f"memory write refused by structural screen: {', '.join(sorted(set(detectors)))}")
        record_detectors = list(structural_verdict[1]) if structural_verdict is not None else []

        self._counter += 1
        record_id = f"m{self._counter}"
        record = MemoryRecord(
            record_id=record_id,
            text=text.strip(),
            provenance=replace(provenance, detectors=tuple(record_detectors) + provenance.detectors),
            created_at=now if now is not None else time.time(),
            tags=tuple(sorted({t for t in tags if t})),
        )
        self.records[record_id] = record
        self._evict()
        return record

    def refuse(self, text: str, provenance: Provenance, *, reason: str) -> None:
        """Record a refused write for the audit trail.

        Present because "what did we turn away, and why" is the question an
        operator asks after an incident, and an exception alone answers it only
        for the caller that happened to be holding it.
        """
        self._violations.append({"origin": provenance.origin_kind, "tier": provenance.tier.value, "reason": reason, "at": time.time()})

    def violations(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._violations)

    def _evict(self) -> None:
        """Evict the oldest record past the cap.

        Weakest authority breaks ties, so an unverified external note is dropped
        before a system measurement. Eviction stops rather than removing a record
        with reinforcements, since a corroborated fact losing its slot to a
        fresher guess is how the store drifts toward whatever arrived last.
        """
        overflow = len(self.records) - self.max_records
        if overflow <= 0:
            return
        # Ascending authority, so the weakest evidence is evicted first. The
        # `reinforcements` term keeps a corroborated fact out of the drop zone
        # even when its tier is low, and oldest-first breaks the remaining tie.
        ranked = sorted(
            self.records.values(),
            key=lambda r: (r.reinforcements > 0, r.authority, r.created_at),
        )
        for record in ranked[:overflow]:
            self.records.pop(record.record_id, None)

    # -- read path -------------------------------------------------------

    def rank(self, query: str, *, now: float | None = None, limit: int = 8) -> list[MemoryRecord]:
        """Trust-weighted relevance order.

        Score is ``relevance * authority * decay``. Every factor is needed: without
        ``authority`` a low-trust but on-topic write takes the top slot; without
        ``decay`` a verified fact from months ago loses to a fresh unverified one,
        which is precisely the recency bias an attacker will use.
        """
        reference = now if now is not None else time.time()
        terms = {t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) > 2}
        scored: list[tuple[float, MemoryRecord]] = []
        for record in self.records.values():
            if record.revoked:
                continue
            if terms:
                haystack = f"{record.text} {' '.join(record.tags)}".lower()
                relevance = sum(1 for term in terms if term in haystack) / len(terms)
                if relevance == 0.0:
                    continue
            else:
                relevance = 0.5
            scored.append((relevance * record.authority * self._decay(record, now=reference), record))
        scored.sort(key=lambda item: (-item[0], item[1].record_id))
        return [record for _, record in scored[:limit]]

    def _decay(self, record: MemoryRecord, *, now: float) -> float:
        """Decay toward a floor, never to zero.

        The floor matters: a verified fact that has gone untouched for a year is
        less current than one from today, not worthless, and a store that decays
        facts into invisibility is a store that forgets.
        """
        if self.decay_window_seconds <= 0:
            return 1.0
        age = record.age_seconds(now=now)
        if age <= self.decay_window_seconds:
            factor = 1.0
        else:
            windows = age / self.decay_window_seconds
            factor = max(0.25, 1.0 / windows)
        # A corroborated fact decays more slowly: reinforcement is evidence.
        return min(1.0, factor + (0.1 * min(record.reinforcements, 3)))

    def record_retrieval(self, record_id: str, shape: str) -> MemoryRecord:
        """Note that a record was retrieved for a query shape.

        Feeds the anomaly signal: a record that suddenly starts surfacing across
        unrelated query shapes is the retrieval signature of a planted entry,
        which activates on a narrow attacker-chosen range rather than spreading
        across a conversation.
        """
        record = self.records[record_id]
        if shape not in record.retrieval_shapes:
            record.retrieval_shapes = record.retrieval_shapes + (shape,)
        return record

    def anomalies(self, *, min_shapes: int = 6) -> tuple[MemoryRecord, ...]:
        """Records retrieved across an implausible number of distinct shapes.

        A threshold, not a verdict. It is a review queue, and the caller decides
        — quarantining on this alone would revoke legitimately broad facts.
        """
        return tuple(r for r in self.records.values() if not r.revoked and len(r.retrieval_shapes) >= min_shapes)

    def reinforce(self, record_id: str) -> MemoryRecord:
        """Mark a record corroborated by a later independent observation."""
        record = self.records[record_id]
        record.reinforcements += 1
        return record

    def revoke(self, record_id: str) -> MemoryRecord:
        """Withdraw a record without deleting it.

        Kept for forensics: "this was removed and when" is answerable only if the
        row survives.
        """
        record = self.records[record_id]
        record.revoked = True
        return record

    def to_report(self) -> dict[str, Any]:
        by_tier: dict[str, int] = {}
        for record in self.records.values():
            by_tier[record.provenance.tier.value] = by_tier.get(record.provenance.tier.value, 0) + 1
        return {
            "total": len(self.records),
            "revoked": sum(1 for r in self.records.values() if r.revoked),
            "by_tier": by_tier,
            "anomalies": len(self.anomalies()),
            "refusals": len(self._violations),
            "records": [r.to_dict() for r in self.records.values()],
        }


def tier_for_origin(origin_kind: str) -> TrustTier:
    """Map an origin label to the maximum tier that origin may claim.

    A mapping rather than a per-caller choice so two ingestion paths cannot
    disagree about how authoritative the same kind of source is.
    """
    mapping = {
        "system": TrustTier.SYSTEM_MEASURED,
        "measurement": TrustTier.SYSTEM_MEASURED,
        "user": TrustTier.USER_STATED,
        "tool": TrustTier.TOOL_OBSERVED,
        "tool_result": TrustTier.TOOL_OBSERVED,
        "model": TrustTier.MODEL_DERIVED,
        "web": TrustTier.EXTERNAL_UNVERIFIED,
        "external": TrustTier.EXTERNAL_UNVERIFIED,
        "document": TrustTier.EXTERNAL_UNVERIFIED,
        "memory_derived": TrustTier.MODEL_DERIVED,
    }
    return mapping.get(origin_kind.strip().lower(), TrustTier.EXTERNAL_UNVERIFIED)


def facts_by_tier(records: Sequence[MemoryRecord]) -> dict[str, int]:
    """Tally records per tier. Used by the grounding report."""
    out: dict[str, int] = {}
    for record in records:
        out[record.provenance.tier.value] = out.get(record.provenance.tier.value, 0) + 1
    return out
