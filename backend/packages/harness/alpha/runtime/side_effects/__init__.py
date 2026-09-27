"""Side-effect tracking: the durable answer to "what might have happened?".

The gap
-------
Alpha already refuses to blindly replay a side effect.
`app.gateway.run_recovery.SafeRunRecoveryService` will not auto-resume a
checkpoint whose pending node is a tool, MCP, shell, browser, write/delete,
payment, or unknown node, and records `stop_reason="recovery_confirmation_required"`
instead. That is correct, and it is a *run-level* signal.

What it cannot do is name the thing that needs confirming. "Run 42 might have
charged someone" is not actionable; "tool call `call_abc` may have charged
someone" is. This package supplies that missing per-effect vocabulary and the
ledger that owns it.

=========================  ===================================================
:mod:`.statuses`           ``SideEffectStatus`` (including ``UNKNOWN``),
                           ``SideEffectLevel``, ``ReconciliationVerdict``, the
                           transition table, and ``SideEffectEntry``.
:mod:`.ledger`             ``SideEffectLedger`` and its reference
                           implementation, with lease-based crash detection and
                           the reclaimer loop.
=========================  ===================================================

The three rules that matter
--------------------------
1. **``UNKNOWN`` is a first-class status, not an error value.** A side-effecting
   operation has three honest outcomes — succeeded, failed, and *we cannot tell*
   — and collapsing the third into either of the first two is how a crash becomes
   a double charge. ``UNKNOWN`` is durable and enumerable so a human or a
   diagnostic agent can be pointed at exactly the set that needs checking.
2. **A worker that dies mid-call is the reason ``UNKNOWN`` exists.** The entry is
   written before the call, becomes ``IN_FLIGHT`` with a lease before it, and only
   a *live* worker may record the real outcome. An expired lease is therefore
   proof that the process that would have known the answer is gone, and
   ``reclaim_expired`` converts that to ``UNKNOWN``. ``PENDING`` is reclaimed too:
   dying between the two writes leaves it exactly as unknown as dying mid-call.
3. **Reconciliation is not optimism.** ``UNKNOWN`` has exactly one exit, through
   ``reconcile()``, and it takes a ``ReconciliationVerdict``. ``UNDETERMINED`` is a
   real answer meaning "I looked and still cannot tell" and it *reopens* the
   entry rather than settling it — recording that as a failure is precisely how a
   duplicate gets created. ``UNKNOWN -> COMPLETED`` is not in the transition table
   at all: a worker that died mid-call cannot be the one to decide the call
   succeeded.

What is deliberately absent
--------------------------
No module here cancels a run, resumes a run, or replays a tool call. The ledger
*records*; `SafeRunRecoveryService` still *decides*. A ledger entry that nobody
reconciles is a visible, queryable gap, which is the honest outcome — not a
permission to guess.

A side-effect record never stores arguments or results, only SHA-256 digests.
These rows are durable, are exported into support bundles, and outlive the thread,
so they must not become a place secrets and personal data accumulate. Two attempts
of the same call still compare equal, which is what a reconciler needs.
"""

from alpha.runtime.side_effects.ledger import (
    InMemorySideEffectLedger,
    ReconciliationResult,
    SideEffectLedger,
    SideEffectReclaimer,
    arguments_digest,
    new_tool_call_id,
    normalize_level,
    result_digest,
)
from alpha.runtime.side_effects.statuses import (
    OPEN_SIDE_EFFECT_STATUSES,
    RECONCILING_SIDE_EFFECT_STATUSES,
    SIDE_EFFECT_LEVELS,
    SIDE_EFFECT_STATUS_TRANSITIONS,
    TERMINAL_SIDE_EFFECT_STATUSES,
    IllegalSideEffectTransition,
    ReconciliationVerdict,
    SideEffectEntry,
    SideEffectLevel,
    SideEffectStatus,
    can_transition,
    status_for_verdict,
    validate_transition,
    verdict_is_settled,
)

__all__ = [
    "OPEN_SIDE_EFFECT_STATUSES",
    "RECONCILING_SIDE_EFFECT_STATUSES",
    "SIDE_EFFECT_LEVELS",
    "SIDE_EFFECT_STATUS_TRANSITIONS",
    "TERMINAL_SIDE_EFFECT_STATUSES",
    "IllegalSideEffectTransition",
    "InMemorySideEffectLedger",
    "ReconciliationResult",
    "ReconciliationVerdict",
    "SideEffectEntry",
    "SideEffectLedger",
    "SideEffectLevel",
    "SideEffectReclaimer",
    "SideEffectStatus",
    "arguments_digest",
    "can_transition",
    "new_tool_call_id",
    "normalize_level",
    "result_digest",
    "status_for_verdict",
    "validate_transition",
    "verdict_is_settled",
]
