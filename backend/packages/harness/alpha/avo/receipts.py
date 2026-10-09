"""Audit receipts: one canonical event per attempted action, including refusals.

The plan's §13 event format, shaped onto the primitives the package already
has. Receipts are sealed through :mod:`alpha.avo.lineage_chain`, so the chain
guarantees are the same ones that module states: tamper-*evident* relative to
the genesis anchor, not tamper-proof against a writer who may rewrite the
whole store. What this module adds is the discipline:

* **Every attempt produces a receipt.** An *allowed* action, a *denied* action,
  an approval request, a budget refusal, an evaluation, a promotion — the
  receipt is minted at the decision, so "no receipt" means "no attempt was
  made", never "an attempt was made and not recorded".
* **The model never mints one.** :class:`ReceiptChain.append` takes the fields
  a server-side observer knows (digests computed, policy version, decision
  made) and defaults ``actor`` to a server role. There is no constructor a
  model call can reach that produces a receipt claiming a decision the gate
  did not make, because the receipt's payload is built from the
  :class:`~alpha.avo.contracts.PolicyDecision` object itself, not from a
  description of it.
* **Claims are checked against the chain.** :meth:`ReceiptChain.verify` is the
  promotion gate's precondition: an invalid chain is
  ``RECEIPT_CHAIN_INVALID`` and the run reports what broke.
* **No secrets.** The receipt carries digests, ids and reason codes; it never
  carries raw tool output, environment values or credentials. ``evidence_refs``
  are references, not contents.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .contracts import PolicyDecision, canonical_json
from .lineage_chain import ChainVerdict, chain_head, seal_entry, verify_entries

__all__ = [
    "EVENT_TYPES",
    "Receipt",
    "ReceiptChain",
    "ReceiptRejected",
    "SCHEMA_VERSION",
]

#: The receipt schema version. A chain mixing versions is refused rather than
#: interpreted leniently: receipts from a different contract are not evidence
#: for this one.
SCHEMA_VERSION = 1

#: The closed set of receipt event types. The plan's taxonomy; adding one is a
#: reviewed change, not a string at a call site.
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "run.admitted",
        "run.refused",
        "action.proposed",
        "action.allowed",
        "action.denied",
        "approval.requested",
        "approval.granted",
        "approval.denied",
        "budget.reserved",
        "budget.exhausted",
        "evaluation.completed",
        "candidate.rejected",
        "candidate.promoted",
        "checkpoint.written",
        "checkpoint.rejected",
        "run.completed",
        "run.recovered",
    }
)


class ReceiptRejected(ValueError):
    """A receipt that cannot enter the chain: wrong schema, unknown type, missing field."""


@dataclass(frozen=True)
class Receipt:
    """One immutable event. Built server-side; the payload is what was decided."""

    receipt_id: str
    event_type: str
    alpha_run_id: str
    avo_run_id: str
    experiment_id: str = ""
    action_id: str = ""
    actor: str = "server"
    action_digest: str = ""
    workspace_digest: str = ""
    policy_version: str = ""
    decision: str = ""
    reason_code: str = ""
    evidence_refs: tuple[str, ...] = ()
    timestamp: float = field(default_factory=time.time)

    def payload(self) -> dict[str, Any]:
        """The canonical form the chain digest is computed over."""
        return {
            "schema_version": SCHEMA_VERSION,
            "receipt_id": self.receipt_id,
            "event_type": self.event_type,
            "alpha_run_id": self.alpha_run_id,
            "avo_run_id": self.avo_run_id,
            "experiment_id": self.experiment_id,
            "action_id": self.action_id,
            "actor": self.actor,
            "action_digest": self.action_digest,
            "workspace_digest": self.workspace_digest,
            "policy_version": self.policy_version,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "evidence_refs": list(self.evidence_refs),
            "timestamp": self.timestamp,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.payload()


@dataclass
class ReceiptChain:
    """The append-only receipt log for one governed run.

    The chain head travels in the checkpoint (see
    :mod:`alpha.avo.checkpointing`), so a resume verifies the log before it
    trusts the state beside it.
    """

    alpha_run_id: str
    avo_run_id: str
    entries: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    @property
    def head(self) -> str:
        return chain_head(self.entries)

    @property
    def next_seq(self) -> int:
        return len(self.entries) + 1

    # ------------------------------------------------------------------
    def append(
        self,
        event_type: str,
        *,
        experiment_id: str = "",
        action_id: str = "",
        actor: str = "server",
        action_digest: str = "",
        workspace_digest: str = "",
        policy_version: str = "",
        decision: str = "",
        reason_code: str = "",
        evidence_refs: tuple[str, ...] | list[str] = (),
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        """Mint and seal one receipt. Raises on anything unchainable."""
        if event_type not in EVENT_TYPES:
            raise ReceiptRejected(f"unknown receipt event type {event_type!r}; the event vocabulary is closed")
        receipt = Receipt(
            receipt_id=f"rcpt_{uuid.uuid4().hex[:12]}",
            event_type=event_type,
            alpha_run_id=self.alpha_run_id,
            avo_run_id=self.avo_run_id,
            experiment_id=experiment_id,
            action_id=action_id,
            actor=actor,
            action_digest=action_digest,
            workspace_digest=workspace_digest,
            policy_version=policy_version,
            decision=decision,
            reason_code=reason_code,
            evidence_refs=tuple(evidence_refs),
            timestamp=timestamp if timestamp is not None else time.time(),
        )
        entry = seal_entry(receipt.payload(), seq=self.next_seq, prev_hash=self.head)
        self.entries.append(entry)
        return entry

    def append_decision(
        self,
        decision: PolicyDecision,
        *,
        event_type: str | None = None,
        workspace_digest: str = "",
        actor: str = "policy_gate",
        evidence_refs: tuple[str, ...] | list[str] = (),
    ) -> dict[str, Any]:
        """Receipt for one policy decision, straight off the decision object.

        The fields come from the decision itself rather than a caller's
        retyping of it, so a receipt cannot disagree with the decision it
        claims to record. ``evidence_refs`` is the one field a caller
        supplies — references to what was consulted (a check id, a rule name),
        never contents.
        """
        inferred = event_type or ("action.allowed" if decision.allowed else "action.denied")
        return self.append(
            inferred,
            experiment_id=decision.experiment_id,
            action_id=decision.action_id,
            actor=actor,
            action_digest=decision.action_digest,
            workspace_digest=workspace_digest,
            policy_version=decision.policy_version,
            decision="allow" if decision.allowed else "deny",
            reason_code=decision.reason_code,
            evidence_refs=evidence_refs,
        )

    # ------------------------------------------------------------------
    def verify(self) -> ChainVerdict:
        """Verify the whole chain. The promotion gate calls this, never trusts it."""
        return verify_entries(self.entries)

    def receipts(self) -> list[dict[str, Any]]:
        """The payloads only, in order — for reports and export."""
        return [entry.get("payload", {}) for entry in self.entries]

    def count(self, event_type: str) -> int:
        return sum(1 for entry in self.entries if entry.get("payload", {}).get("event_type") == event_type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "alpha_run_id": self.alpha_run_id,
            "avo_run_id": self.avo_run_id,
            "head": self.head,
            "entries": list(self.entries),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ReceiptChain:
        """Rebuild a chain. The schema version is checked, not assumed."""
        version = payload.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ReceiptRejected(f"receipt chain carries schema_version {version!r}, this build speaks {SCHEMA_VERSION}")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise ReceiptRejected("receipt chain entries must be a list")
        chain = cls(
            alpha_run_id=str(payload.get("alpha_run_id", "")),
            avo_run_id=str(payload.get("avo_run_id", "")),
        )
        chain.entries = entries
        return chain

    def digest(self) -> str:
        """Canonical digest of the chain head + length, for checkpoint binding."""
        return hashlib.sha256(canonical_json({"head": self.head, "count": len(self.entries)}).encode("utf-8")).hexdigest()
