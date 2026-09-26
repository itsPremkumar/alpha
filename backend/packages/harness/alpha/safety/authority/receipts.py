"""Decision receipts: a tamper-evident record of *who decided what, and why*.

Why a receipt and not an event
------------------------------
``alpha/bots/governance_ledger.py`` is a good event log.  It records that a
profile was created, re-scoped, retired, or refused.  What it cannot answer is
the question an operator actually asks after an incident: *who decided this,
under which policy rule, from which inputs, and what alternatives were
rejected?*  That is a different artefact.  An event log cannot be retrofitted
into a receipt, because the alternatives considered and the inputs weighed
exist only in the moment of the decision and are gone afterwards.  Receipts
must therefore be written AT decision time, by the code making the decision.

This is the artefact OpenClaw's ``openclaw audit`` produces -- activity records
plus EXECUTION IDENTITY plus DECISION RECEIPTS
(https://docs.openclaw.ai/start/why-openclaw).  alpha had the first of the
three.  This module is the second and third.

What is guaranteed here
-----------------------
* **Execution identity is never collapsed.**  A delegated child's action is
  attributed to the CHILD; the parent is recorded as ``delegator``.  Merging
  them would make "who did this" unanswerable in exactly the case where it
  matters most -- an agent acting on a delegation.
* **A receipt whose policy rule cannot be named is a FINDING, not a receipt.**
  It is still written, and it is written flagged, so the gap is countable
  rather than invisible.
* **Append-only and tamper-evident.**  Each receipt hashes its own canonical
  payload together with its predecessor's hash.  Editing a historical receipt
  breaks the chain from that point on, and :meth:`ReceiptChain.verify` says
  where.

This is tamper-*evident*, not tamper-proof: the same process that writes the
chain can also rewrite it, and an attacker with write access to the file can
recompute the chain.  Making the chain unforgeable needs a signature whose key
lives outside this process, which is a boundary, not a control -- see the
report's "cannot be enforced in-process" section.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = 1


# ---------------------------------------------------------------------------
# Execution identity
# ---------------------------------------------------------------------------


class ActorKind(StrEnum):
    """The four kinds of actor an execution can be attributed to.

    Kept as four distinct kinds rather than a role string because "who did
    this" must never be ambiguous: an automated system, a human, a top-level
    agent and a delegated child are four different accountability stories.
    """

    HUMAN = "human"
    AGENT = "agent"
    CHILD_AGENT = "child_agent"
    AUTOMATED_SYSTEM = "automated_system"


class ReceiptFlag(StrEnum):
    """Why a receipt is not a clean receipt."""

    #: The policy rule applied could not be named.  A decision with no
    #: nameable rule is a FINDING: there is nothing to review it against.
    UNNAMEABLE_POLICY_RULE = "receipt.unnameable_policy_rule"
    #: The actor's identity was asserted by model-authored content.
    MODEL_ASSERTED_IDENTITY = "receipt.model_asserted_identity"
    #: The decision was taken on a turn tainted by untrusted content.
    TAINTED_TURN = "receipt.tainted_turn"
    #: No work reference was supplied, so the receipt cannot answer "why did
    #: this happen" for an authorised action.
    NO_WORK_REFERENCE = "receipt.no_work_reference"
    #: No rejected alternative was recorded for an allow decision.
    NO_REJECTED_ALTERNATIVE = "receipt.no_rejected_alternative"
    #: The receipt is a refusal, not an allow.
    REFUSAL = "receipt.refusal"


class ReceiptError(RuntimeError):
    """Base class for receipt failures. Not a ``ValueError``: not caller error."""


class IdentityError(ReceiptError):
    """The execution identity is not a valid attribution."""


class ChainIntegrityError(ReceiptError):
    """The receipt chain does not verify."""


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    """Who executed, and on whose behalf.

    ``delegator`` is only meaningful for :attr:`ActorKind.CHILD_AGENT`, and is
    REQUIRED for it.  A child with no delegator is unattributable work, and
    unattributable work is refused rather than recorded as the parent's.

    ``model_asserted`` marks an identity that came from model-authored input.
    Such an identity is retained for the audit trail but is never authoritative
    for its own privileges, its own policy scope, or its own taint state.
    """

    kind: ActorKind
    principal_id: str
    delegator: ExecutionIdentity | None = None
    authenticated_by: str = ""
    model_asserted: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ActorKind(str(self.kind)))
        if not str(self.principal_id or "").strip():
            raise IdentityError("an execution identity needs a non-empty principal id")
        if self.kind is ActorKind.CHILD_AGENT:
            if self.delegator is None:
                raise IdentityError(
                    f"child agent {self.principal_id!r} has no delegator; a delegated child's "
                    "action must name who delegated it, or it is unattributable"
                )
            if self.delegator.kind is ActorKind.CHILD_AGENT:
                raise IdentityError(
                    f"child agent {self.principal_id!r} names another child as its delegator; "
                    "a delegation chain must terminate at an agent, a human, or the system"
                )
            if self.delegator.principal_id == self.principal_id:
                raise IdentityError(f"{self.principal_id!r} cannot delegate to itself")
        elif self.delegator is not None:
            raise IdentityError(
                f"{self.kind.value} {self.principal_id!r} may not carry a delegator; only a "
                "child agent is acting on someone else's authority"
            )

    @property
    def is_authoritative(self) -> bool:
        """May this identity be trusted about its OWN privileges or scope?"""

        return not self.model_asserted

    @property
    def delegator_id(self) -> str:
        return self.delegator.principal_id if self.delegator is not None else ""

    def actor_line(self) -> str:
        """The single attribution line an operator reads.

        A child's action reads as ``child:researcher-3 (delegated by
        agent:lead)``, never as ``agent:lead``.
        """

        who = f"{self.kind.value}:{self.principal_id}"
        if self.delegator is not None:
            who = f"{who} (delegated by {self.delegator.kind.value}:{self.delegator.principal_id})"
        if self.model_asserted:
            who = f"{who} [identity model-asserted; not authoritative]"
        return who

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "principal_id": self.principal_id,
            "delegator": self.delegator.to_dict() if self.delegator is not None else None,
            "authenticated_by": self.authenticated_by,
            "model_asserted": self.model_asserted,
            "actor_line": self.actor_line(),
        }


def human(principal_id: str, *, authenticated_by: str = "session") -> ExecutionIdentity:
    return ExecutionIdentity(kind=ActorKind.HUMAN, principal_id=principal_id, authenticated_by=authenticated_by)


def agent(principal_id: str, *, authenticated_by: str = "internal-token") -> ExecutionIdentity:
    return ExecutionIdentity(kind=ActorKind.AGENT, principal_id=principal_id, authenticated_by=authenticated_by)


def automated_system(principal_id: str, *, authenticated_by: str = "scheduler") -> ExecutionIdentity:
    return ExecutionIdentity(
        kind=ActorKind.AUTOMATED_SYSTEM, principal_id=principal_id, authenticated_by=authenticated_by
    )


def child_agent(
    principal_id: str,
    *,
    delegator: ExecutionIdentity,
    authenticated_by: str = "internal-token",
    model_asserted: bool = False,
) -> ExecutionIdentity:
    return ExecutionIdentity(
        kind=ActorKind.CHILD_AGENT,
        principal_id=principal_id,
        delegator=delegator,
        authenticated_by=authenticated_by,
        model_asserted=model_asserted,
    )


def model_asserted_identity(kind: str, principal_id: str) -> ExecutionIdentity:
    """Build an identity the MODEL claimed for itself.

    Retained so the trail shows what the model said, and marked so nothing
    downstream may treat it as authoritative for privileges, scope, or taint.
    """

    return ExecutionIdentity(
        kind=ActorKind(str(kind)),
        principal_id=str(principal_id),
        authenticated_by="model-output",
        model_asserted=True,
    )


# ---------------------------------------------------------------------------
# The receipt
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RejectedAlternative:
    """An option that was considered and not taken, and WHY."""

    option: str
    reason: str

    def __post_init__(self) -> None:
        if not str(self.option or "").strip():
            raise ReceiptError("a rejected alternative needs a name")
        if not str(self.reason or "").strip():
            raise ReceiptError(
                f"rejected alternative {self.option!r} has no reason; 'rejected' without a "
                "reason is not evidence that a decision was made"
            )

    def to_dict(self) -> dict[str, str]:
        return {"option": self.option, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class DecisionReceipt:
    """One decision, recorded at the moment it was made.

    ``hash`` covers every other field plus ``prev_hash``, so a receipt cannot be
    edited, removed, or reordered without the chain breaking.
    """

    receipt_id: str
    seq: int
    decision: str
    identity: ExecutionIdentity
    policy_rule: str
    scope: str
    inputs: tuple[str, ...]
    rejected_alternatives: tuple[RejectedAlternative, ...]
    outcome: str
    work_ref: str
    decided_at: str
    effective_policy: Mapping[str, Any] = field(default_factory=dict)
    taint_sources: tuple[str, ...] = ()
    flags: tuple[ReceiptFlag, ...] = ()
    prev_hash: str = ""
    hash: str = ""

    # -- hashing ------------------------------------------------------------

    def canonical_payload(self) -> str:
        """Deterministic JSON over every field except ``hash`` itself."""

        payload = {
            "schema_version": SCHEMA_VERSION,
            "receipt_id": self.receipt_id,
            "seq": self.seq,
            "decision": self.decision,
            "identity": self.identity.to_dict(),
            "policy_rule": self.policy_rule,
            "scope": self.scope,
            "inputs": list(self.inputs),
            "rejected_alternatives": [item.to_dict() for item in self.rejected_alternatives],
            "outcome": self.outcome,
            "work_ref": self.work_ref,
            "decided_at": self.decided_at,
            "effective_policy": _canonical_mapping(self.effective_policy),
            "taint_sources": list(self.taint_sources),
            "flags": sorted(item.value for item in self.flags),
            "prev_hash": self.prev_hash,
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    def compute_hash(self) -> str:
        digest = hashlib.sha256(self.canonical_payload().encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    # -- reporting ----------------------------------------------------------

    @property
    def flagged(self) -> bool:
        return bool(self.flags)

    def why_flagged(self) -> tuple[str, ...]:
        return tuple(item.value for item in self.flags)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "receipt_id": self.receipt_id,
            "seq": self.seq,
            "decision": self.decision,
            "identity": self.identity.to_dict(),
            "actor_line": self.identity.actor_line(),
            "policy_rule": self.policy_rule,
            "scope": self.scope,
            "inputs": list(self.inputs),
            "rejected_alternatives": [item.to_dict() for item in self.rejected_alternatives],
            "outcome": self.outcome,
            "work_ref": self.work_ref,
            "decided_at": self.decided_at,
            "effective_policy": dict(self.effective_policy),
            "taint_sources": list(self.taint_sources),
            "flags": sorted(item.value for item in self.flags),
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True)

    def render(self) -> str:
        """The one-line answer to "who decided this, under what rule, why"."""

        alternatives = "; ".join(f"rejected {item.option} ({item.reason})" for item in self.rejected_alternatives)
        flags = f" FLAGGED[{','.join(self.why_flagged())}]" if self.flagged else ""
        return (
            f"#{self.seq} {self.decision} by {self.identity.actor_line()} "
            f"rule={self.policy_rule or '<unnamed>'} scope={self.scope or '<none>'} "
            f"outcome={self.outcome} work={self.work_ref or '<none>'}{flags}"
            + (f" | {alternatives}" if alternatives else "")
        )


def _canonical_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    return json.loads(json.dumps(dict(value), ensure_ascii=True, sort_keys=True, default=str))


#: Policy rules this module can name.  A receipt citing anything outside this
#: set is still written, but it is written FLAGGED: a rule nobody can resolve
#: is not a rule, and pretending otherwise is how "policy is code" quietly
#: becomes "policy is a sentence in a log".
KNOWN_POLICY_RULES: Final[frozenset[str]] = frozenset(
    {
        # authority ceiling
        "CEILING-WITHIN-RANK-001",
        "CEILING-CREATOR-GRANT-001",
        "CEILING-UNTRUSTED-RANK-001",
        "CEILING-POPULATION-001",
        "CEILING-PROTECTED-COMPONENT-001",
        "CEILING-DEMOTED-ON-USE-001",
        # scopes
        "SCOPE-BASE-001",
        "SCOPE-BASE-002",
        "SCOPE-DENY-001",
        "SCOPE-RANK-001",
        "SCOPE-CHAN-001",
        "SCOPE-ROLE-001",
        "SCOPE-REVIEW-001",
        "SCOPE-EVID-001",
        "SCOPE-REVIEW-002",
        "SCOPE-EVID-002",
        "SCOPE-CEIL-001",
        "SCOPE-MATCH-000",
        "SCOPE-LOAD-REFUSED-001",
        # taint
        "TAINT-BOUNDS-AUTHORITY-001",
        "TAINT-BLOCKS-EVIDENCE-001",
        "TAINT-BLOCKS-APPROVAL-001",
        "TAINT-CLEAR-REFUSED-001",
        # boundaries
        "BOUNDARY-INBOUND-DEFAULT-DENY-001",
        "BOUNDARY-APPROVAL-FAIL-CLOSED-001",
        "BOUNDARY-CREDENTIAL-OWNER-ISOLATION-001",
        "BOUNDARY-STARTUP-REFUSED-001",
    }
)


def classify_flags(
    *,
    policy_rule: str,
    outcome: str,
    identity: ExecutionIdentity,
    work_ref: str,
    rejected_alternatives: tuple[RejectedAlternative, ...],
    taint_sources: tuple[str, ...] = (),
) -> tuple[ReceiptFlag, ...]:
    """Decide which findings a receipt carries. Pure, so it is directly testable."""

    flags: list[ReceiptFlag] = []
    if not str(policy_rule or "").strip():
        flags.append(ReceiptFlag.UNNAMEABLE_POLICY_RULE)
    elif policy_rule not in KNOWN_POLICY_RULES:
        flags.append(ReceiptFlag.UNNAMEABLE_POLICY_RULE)
    if identity.model_asserted:
        flags.append(ReceiptFlag.MODEL_ASSERTED_IDENTITY)
    if taint_sources:
        flags.append(ReceiptFlag.TAINTED_TURN)
    if not str(work_ref or "").strip():
        flags.append(ReceiptFlag.NO_WORK_REFERENCE)
    if not rejected_alternatives and str(outcome or "").strip().lower() in {"allowed", "granted", "applied"}:
        flags.append(ReceiptFlag.NO_REJECTED_ALTERNATIVE)
    if str(outcome or "").strip().lower() in {"refused", "denied"}:
        flags.append(ReceiptFlag.REFUSAL)
    return tuple(flags)


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """The result of verifying a chain."""

    ok: bool
    receipts_checked: int
    broken_at_seq: int | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "receipts_checked": self.receipts_checked,
            "broken_at_seq": self.broken_at_seq,
            "reason": self.reason,
        }


def default_receipt_dir() -> Path:
    """Where receipts live. Under the runtime home, so tests are isolated."""

    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "governance" / "receipts"
    except Exception:
        return Path(os.environ.get("AGENT_WORKSPACE_HOME", ".")) / "governance" / "receipts"


class ReceiptChain:
    """Append-only, hash-chained decision receipts.

    The chain is per-subject by construction: pass a ``chain_id`` and the file
    is ``<chain_id>.jsonl``.  A receipt can only be added, never updated --
    there is no ``update``/``delete``/``pop`` method on this class, and the list
    is exposed read-only through :meth:`entries`.
    """

    def __init__(self, *, chain_id: str = "default", directory: str | Path | None = None) -> None:
        if not str(chain_id or "").strip():
            raise ReceiptError("a receipt chain needs a chain id")
        self._chain_id = str(chain_id).strip()
        self._directory = Path(directory) if directory is not None else default_receipt_dir()
        self._lock = threading.RLock()
        self._receipts: list[DecisionReceipt] = []

    # -- identity ------------------------------------------------------------

    @property
    def chain_id(self) -> str:
        return self._chain_id

    @property
    def path(self) -> Path:
        return self._directory / f"{self._chain_id}.jsonl"

    def entries(self) -> tuple[DecisionReceipt, ...]:
        """Read-only view. The returned tuple cannot mutate the chain."""

        with self._lock:
            return tuple(self._receipts)

    def head_hash(self) -> str:
        with self._lock:
            return self._receipts[-1].hash if self._receipts else ""

    def __len__(self) -> int:
        with self._lock:
            return len(self._receipts)

    # -- append --------------------------------------------------------------

    def append(
        self,
        *,
        decision: str,
        identity: ExecutionIdentity,
        outcome: str,
        policy_rule: str = "",
        scope: str = "",
        inputs: Iterable[str] = (),
        rejected_alternatives: Iterable[RejectedAlternative] = (),
        work_ref: str = "",
        decided_at: str = "",
        effective_policy: Mapping[str, Any] | None = None,
        taint_sources: Iterable[str] = (),
    ) -> DecisionReceipt:
        """Write one receipt. There is no path in this class that edits one."""

        if not isinstance(identity, ExecutionIdentity):
            raise ReceiptError("a decision receipt needs an ExecutionIdentity")
        alternatives = tuple(rejected_alternatives)
        taint = tuple(str(item) for item in taint_sources)
        flags = classify_flags(
            policy_rule=policy_rule,
            outcome=outcome,
            identity=identity,
            work_ref=work_ref,
            rejected_alternatives=alternatives,
            taint_sources=taint,
        )
        with self._lock:
            seq = len(self._receipts) + 1
            prev = self._receipts[-1].hash if self._receipts else ""
            body = DecisionReceipt(
                receipt_id=f"{self._chain_id}-{seq:06d}",
                seq=seq,
                decision=str(decision).strip(),
                identity=identity,
                policy_rule=str(policy_rule or "").strip(),
                scope=str(scope or "").strip(),
                inputs=tuple(str(item) for item in inputs),
                rejected_alternatives=alternatives,
                outcome=str(outcome).strip(),
                work_ref=str(work_ref or "").strip(),
                decided_at=str(decided_at or "").strip(),
                effective_policy=dict(effective_policy or {}),
                taint_sources=taint,
                flags=flags,
                prev_hash=prev,
            )
            receipt = DecisionReceipt(
                receipt_id=body.receipt_id,
                seq=body.seq,
                decision=body.decision,
                identity=body.identity,
                policy_rule=body.policy_rule,
                scope=body.scope,
                inputs=body.inputs,
                rejected_alternatives=body.rejected_alternatives,
                outcome=body.outcome,
                work_ref=body.work_ref,
                decided_at=body.decided_at,
                effective_policy=body.effective_policy,
                taint_sources=body.taint_sources,
                flags=body.flags,
                prev_hash=body.prev_hash,
                hash=body.compute_hash(),
            )
            self._receipts.append(receipt)
        return receipt

    # -- verification --------------------------------------------------------

    def verify(self) -> ChainVerification:
        """Recompute every hash and every link. Detects edit, removal, reorder."""

        with self._lock:
            receipts = list(self._receipts)
        previous_hash = ""
        for index, receipt in enumerate(receipts, start=1):
            if receipt.seq != index:
                return ChainVerification(
                    ok=False,
                    receipts_checked=index - 1,
                    broken_at_seq=receipt.seq,
                    reason=(
                        f"receipt {receipt.receipt_id!r} claims seq {receipt.seq} at position "
                        f"{index}: a receipt was removed or reordered"
                    ),
                )
            if receipt.prev_hash != previous_hash:
                return ChainVerification(
                    ok=False,
                    receipts_checked=index - 1,
                    broken_at_seq=receipt.seq,
                    reason=(
                        f"receipt {receipt.receipt_id!r} chains to {receipt.prev_hash[:23]!r} but "
                        f"its predecessor hashes to {previous_hash[:23]!r}: a historical receipt "
                        "was edited"
                    ),
                )
            if receipt.compute_hash() != receipt.hash:
                return ChainVerification(
                    ok=False,
                    receipts_checked=index - 1,
                    broken_at_seq=receipt.seq,
                    reason=(
                        f"receipt {receipt.receipt_id!r} contents do not match its recorded hash: "
                        f"stored {receipt.hash[:23]!r}, recomputed {receipt.compute_hash()[:23]!r}"
                    ),
                )
            previous_hash = receipt.hash
        return ChainVerification(ok=True, receipts_checked=len(receipts))

    def assert_intact(self) -> None:
        verdict = self.verify()
        if not verdict.ok:
            raise ChainIntegrityError(
                f"decision receipt chain {self._chain_id!r} does not verify: {verdict.reason}"
            )

    # -- persistence ---------------------------------------------------------

    def persist(self) -> Path:
        """Append the chain to disk as JSON Lines.

        Opened in append mode and written as a whole line per receipt, so a
        concurrent writer cannot interleave a partial record.
        """

        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            lines = [receipt.to_json() for receipt in self._receipts]
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(line + "\n")
        return path

    @classmethod
    def load(cls, *, chain_id: str = "default", directory: str | Path | None = None) -> ReceiptChain:
        """Rebuild a chain from disk. Any malformed line is a hard failure.

        A ledger that silently drops a line it cannot parse is exactly the
        "record that can be edited after the fact" failure this module exists
        to remove.
        """

        chain = cls(chain_id=chain_id, directory=directory)
        path = chain.path
        if not path.exists():
            return chain
        with path.open("r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                    receipt = _receipt_from_dict(payload)
                except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
                    raise ChainIntegrityError(
                        f"receipt chain {chain_id!r} line {lineno} is not a readable receipt: {exc}"
                    ) from exc
                chain._receipts.append(receipt)
        return chain

    # -- linking receipts to the work they authorised -----------------------

    def receipts_for_work(self, work_ref: str) -> tuple[DecisionReceipt, ...]:
        """Every receipt that authorised (or refused) *work_ref*.

        This is the "why did this happen" query: one call, not an
        archaeology project across an event log.
        """

        target = str(work_ref or "").strip()
        if not target:
            return ()
        with self._lock:
            return tuple(receipt for receipt in self._receipts if receipt.work_ref == target)

    def receipts_for_actor(self, principal_id: str) -> tuple[DecisionReceipt, ...]:
        target = str(principal_id or "").strip().lower()
        with self._lock:
            return tuple(
                receipt
                for receipt in self._receipts
                if receipt.identity.principal_id.lower() == target
                or receipt.identity.delegator_id.lower() == target
            )

    def flagged(self) -> tuple[DecisionReceipt, ...]:
        with self._lock:
            return tuple(receipt for receipt in self._receipts if receipt.flagged)

    def reset(self) -> None:
        """Drop in-memory state. Test/operator helper; persistence is separate."""

        with self._lock:
            self._receipts.clear()


def _receipt_from_dict(payload: Mapping[str, Any]) -> DecisionReceipt:
    identity_payload = payload["identity"]
    delegator_payload = identity_payload.get("delegator")
    identity = ExecutionIdentity(
        kind=ActorKind(identity_payload["kind"]),
        principal_id=str(identity_payload["principal_id"]),
        delegator=(
            ExecutionIdentity(
                kind=ActorKind(delegator_payload["kind"]),
                principal_id=str(delegator_payload["principal_id"]),
                delegator=None,
                authenticated_by=str(delegator_payload.get("authenticated_by", "")),
                model_asserted=bool(delegator_payload.get("model_asserted", False)),
            )
            if isinstance(delegator_payload, Mapping)
            else None
        ),
        authenticated_by=str(identity_payload.get("authenticated_by", "")),
        model_asserted=bool(identity_payload.get("model_asserted", False)),
    )
    alternatives = tuple(
        RejectedAlternative(option=str(item["option"]), reason=str(item["reason"]))
        for item in payload.get("rejected_alternatives", ())
    )
    return DecisionReceipt(
        receipt_id=str(payload["receipt_id"]),
        seq=int(payload["seq"]),
        decision=str(payload["decision"]),
        identity=identity,
        policy_rule=str(payload.get("policy_rule", "")),
        scope=str(payload.get("scope", "")),
        inputs=tuple(str(item) for item in payload.get("inputs", ())),
        rejected_alternatives=alternatives,
        outcome=str(payload.get("outcome", "")),
        work_ref=str(payload.get("work_ref", "")),
        decided_at=str(payload.get("decided_at", "")),
        effective_policy=dict(payload.get("effective_policy", {}) or {}),
        taint_sources=tuple(str(item) for item in payload.get("taint_sources", ())),
        flags=tuple(ReceiptFlag(item) for item in payload.get("flags", ())),
        prev_hash=str(payload.get("prev_hash", "")),
        hash=str(payload.get("hash", "")),
    )


#: Process-wide chain used by the authority chokepoints in
#: :mod:`alpha.bots.authority_ceiling`.  Keyed by scope/ceiling so two
#: installations writing to one runtime home do not interleave.
_chains: dict[str, ReceiptChain] = {}
_chains_lock = threading.RLock()


def get_receipt_chain(chain_id: str = "authority") -> ReceiptChain:
    """The process-wide receipt chain for *chain_id*."""

    with _chains_lock:
        chain = _chains.get(chain_id)
        if chain is None:
            chain = ReceiptChain(chain_id=chain_id)
            _chains[chain_id] = chain
        return chain


__all__ = [
    "KNOWN_POLICY_RULES",
    "SCHEMA_VERSION",
    "ActorKind",
    "ChainIntegrityError",
    "ChainVerification",
    "DecisionReceipt",
    "ExecutionIdentity",
    "IdentityError",
    "ReceiptChain",
    "ReceiptError",
    "ReceiptFlag",
    "RejectedAlternative",
    "agent",
    "automated_system",
    "child_agent",
    "classify_flags",
    "default_receipt_dir",
    "get_receipt_chain",
    "human",
    "model_asserted_identity",
]
