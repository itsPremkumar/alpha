"""Honest telemetry for deferred tool discovery.

A promotion metric is evidence, so this module refuses to report a success it
cannot prove. Four invariants define it, and the tests in
``backend/tests/test_tool_discovery_telemetry.py`` pin each one:

* **Only a verified promotion counts.** ``tool_search`` *proposes* names. The
  outer ``SkillToolPolicyMiddleware`` filters the returned ``Command``
  afterwards, so a proposal is not a promotion. A promotion is counted only
  when the surviving names are read back from the policy decision that the
  enforcing middleware itself published for this step.
* **Unknown is its own outcome.** An absent, foreign, or malformed policy
  decision is recorded as ``unverified`` -- never as a promotion, and never
  collapsed into a ``denied``/zero that would read like a clean answer.
* **An error outcome is an error outcome.** A raised call, an error-valued
  ``ToolMessage``, or an error string returned by a handler is recorded as
  ``error`` and can never increment the promotion counter.
* **No guessing.** Every negative verdict carries a stable machine-readable
  ``reason``, so "we could not tell" is always distinguishable from "the policy
  removed it".

The recorder is a plain object held on the per-run LangGraph context under a
server-owned ``__``-prefixed key -- the same pattern as the run journal and the
authorization outcome. That keeps counters per-run (no process-global mutable
state, no cross-test leakage) and means a direct ``tool.invoke(...)`` outside a
graph records nothing at all rather than inventing a success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from langchain_core.messages import ToolMessage

from alpha.runtime.secret_context import SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY

# ── Outcomes ──
# Deliberately distinct values. ``UNVERIFIED`` is the project's rule made
# literal: unavailable / error / insufficient are separate non-passing
# outcomes, so an unmeasurable promotion can never be reported as zero work
# done and never as a success.

PROMOTED = "promoted"
DENIED = "denied"
NO_MATCH = "no_match"
UNVERIFIED = "unverified"
ERROR = "error"

OUTCOMES: tuple[str, ...] = (PROMOTED, DENIED, NO_MATCH, UNVERIFIED, ERROR)

# ── Reasons ──
# Stable, greppable strings. A dashboard can group by these without parsing a
# message, and a test can assert that "unknown" and "denied" stayed separate.

REASON_ALLOWLIST = "policy_allowlist"
REASON_UNRESTRICTED = "policy_unrestricted"
REASON_NO_RUNTIME_CONTEXT = "no_runtime_context"
REASON_NO_POLICY_DECISION = "no_policy_decision"
REASON_MALFORMED_POLICY_DECISION = "malformed_policy_decision"
REASON_FOREIGN_POLICY_DECISION = "foreign_policy_decision"
REASON_NO_PROPOSED_NAMES = "no_proposed_names"
REASON_CALL_SUCCEEDED = "call_succeeded"
REASON_CALL_RAISED = "call_raised"
REASON_ERROR_RESULT = "error_result"
REASON_ERROR_STATUS = "error_status"
REASON_UNVERIFIED_RESULT = "unverified_result"
REASON_NO_RESULTS = "no_results"
REASON_RESULTS_RETURNED = "results_returned"
REASON_SCHEMA_RETURNED = "schema_returned"

# ── Policy-decision contract ──
# Mirrors ``agents/middlewares/skill_tool_policy_middleware.py``. Those
# constants are module-private, so they are restated here and pinned equal by
# ``test_policy_decision_contract_matches_the_enforcing_middleware``. If the
# enforcing side bumps its version or adds a source, this module must follow or
# every promotion turns ``unverified`` (the safe direction) until it does.

POLICY_DECISION_VERSION = 2
POLICY_SOURCES: frozenset[str] = frozenset({"passive", "slash", "skill_context"})

# Per-run context key for the recorder. Server-owned and ``__``-prefixed so
# ``build_run_config`` / ``strip_internal_context_keys`` drop any caller-supplied
# copy at run admission.
TELEMETRY_CONTEXT_KEY = "__tool_discovery_telemetry"

_VERIFIED_REASONS = frozenset({REASON_ALLOWLIST, REASON_UNRESTRICTED})


@dataclass(frozen=True)
class PromotionVerdict:
    """One honest outcome of a discovery call.

    ``promoted`` holds only names that a *verified* policy decision permitted.
    It is empty for every non-``promoted`` outcome, so counting promotions
    cannot be short-circuited by a later ``if promoted:`` truthiness check.
    """

    outcome: str
    reason: str
    promoted: tuple[str, ...] = ()


def read_policy_decision(context: Any) -> tuple[frozenset[str] | None, str]:
    """Return ``(allowed_names, reason)`` for the decision published this step.

    ``allowed_names`` is ``None`` only when the decision is well-formed and
    explicitly unrestricted. Every rejection returns ``(None, <reason>)`` where
    the reason is a *negative* one, so a caller cannot mistake "could not
    determine" for "allowed everything": only :data:`REASON_UNRESTRICTED` pairs
    with ``None``.
    """
    if not isinstance(context, dict):
        return None, REASON_NO_RUNTIME_CONTEXT
    if SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY not in context:
        return None, REASON_NO_POLICY_DECISION
    decision = context[SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY]
    if not isinstance(decision, dict):
        return None, REASON_MALFORMED_POLICY_DECISION

    version = decision.get("version")
    if type(version) is not int or version != POLICY_DECISION_VERSION:
        return None, REASON_FOREIGN_POLICY_DECISION
    owner_token = decision.get("owner_token")
    if not isinstance(owner_token, str) or not owner_token:
        return None, REASON_MALFORMED_POLICY_DECISION
    source = decision.get("source")
    if not isinstance(source, str) or source not in POLICY_SOURCES:
        return None, REASON_FOREIGN_POLICY_DECISION
    active_paths = decision.get("active_paths")
    if not isinstance(active_paths, list) or not all(isinstance(path, str) for path in active_paths):
        return None, REASON_MALFORMED_POLICY_DECISION

    allowed = decision.get("allowed_names")
    if allowed is None:
        return None, REASON_UNRESTRICTED
    if not isinstance(allowed, list) or not all(isinstance(name, str) for name in allowed):
        return None, REASON_MALFORMED_POLICY_DECISION
    return frozenset(allowed), REASON_ALLOWLIST


def evaluate_proposed_promotions(proposed: Any, context: Any, *, deferred: frozenset[str] = frozenset()) -> PromotionVerdict:
    """Decide what, if anything, a set of proposed names actually promoted.

    ``deferred`` is the catalog-scoped deferred set for this agent build; a
    proposed name outside it is not a promotion of a deferred tool and is
    dropped before the policy check.

    Never returns a ``promoted`` verdict unless ``context`` carries a
    well-formed, current policy decision. That is what keeps a denied, foreign,
    or unmeasured call out of the success counter.
    """
    if not isinstance(proposed, (list, tuple, set, frozenset)):
        return PromotionVerdict(UNVERIFIED, REASON_MALFORMED_POLICY_DECISION)
    names = sorted({name for name in proposed if isinstance(name, str) and name in deferred})
    if not names:
        return PromotionVerdict(NO_MATCH, REASON_NO_PROPOSED_NAMES)

    allowed, reason = read_policy_decision(context)
    if reason not in _VERIFIED_REASONS:
        # Absent, foreign, or malformed: unverified, never a success.
        return PromotionVerdict(UNVERIFIED, reason)

    permitted = names if allowed is None else [name for name in names if name in allowed]
    removed = [name for name in names if name not in permitted]
    if removed:
        # Partly or wholly denied. Surviving names stay verified; the removed
        # ones are reported by this outcome and never counted as promotions.
        return PromotionVerdict(DENIED, REASON_ALLOWLIST, tuple(permitted))
    return PromotionVerdict(PROMOTED, reason, tuple(permitted))


def classify_catalog_result(result: Any) -> tuple[str, str]:
    """Classify a ``catalog.call()`` result as a success or an error outcome.

    A non-throwing ``call()`` is not a success: the handler may have returned a
    ``ToolMessage`` whose ``status`` is ``"error"``, or a plain string carrying
    an error (the shape this module's own callers already produce). An
    unrecognised ``ToolMessage.status`` is ``unverified`` rather than success,
    because "we did not recognise it" is not evidence that it worked.
    """
    if isinstance(result, ToolMessage):
        status = result.status
        if status == "error":
            return ERROR, REASON_ERROR_STATUS
        if status in (None, "success"):
            return PROMOTED, REASON_CALL_SUCCEEDED
        return UNVERIFIED, REASON_UNVERIFIED_RESULT
    if isinstance(result, str) and result[:5].lower() == "error":
        return ERROR, REASON_ERROR_RESULT
    return PROMOTED, REASON_CALL_SUCCEEDED


@dataclass
class DiscoveryTelemetry:
    """Per-run discovery counters. One instance per run, never a module global."""

    promotions_verified: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, int] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)
    call_outcomes: dict[str, int] = field(default_factory=dict)
    verified_names: set[str] = field(default_factory=set)
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def record_promotion(self, verdict: PromotionVerdict) -> None:
        """Record one promotion verdict. The only write path to the success counter."""
        with self._lock:
            self.outcomes[verdict.outcome] = self.outcomes.get(verdict.outcome, 0) + 1
            self.reasons[verdict.reason] = self.reasons.get(verdict.reason, 0) + 1
            # Reads the verdict's verified names, never the proposed list.
            self.promotions_verified += len(verdict.promoted)
            self.verified_names.update(verdict.promoted)

    def record_call(self, *, kind: str, outcome: str, reason: str) -> None:
        """Record one catalog call.

        Deliberately writes to ``call_outcomes``, never to ``outcomes``: a
        ``catalog_tool_call`` success is not a deferred-tool promotion, and
        sharing one map would let a reader see ``outcomes["promoted"] == 1``
        next to ``promotions_verified == 0``.
        """
        with self._lock:
            self.calls[kind] = self.calls.get(kind, 0) + 1
            self.call_outcomes[outcome] = self.call_outcomes.get(outcome, 0) + 1
            self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        """Plain, JSON-safe counters with every outcome key always present.

        Absent outcomes read as ``0`` *and* as an explicit key, so a consumer
        can tell "no calls yet" from "the counter was never wired" by checking
        for the key rather than inferring from a missing field.

        ``outcomes`` counts deferred-promotion verdicts only; ``call_outcomes``
        counts catalog calls. They are separate on purpose -- see
        :meth:`record_call`.
        """
        with self._lock:
            return {
                "promotions_verified": self.promotions_verified,
                "outcomes": {outcome: self.outcomes.get(outcome, 0) for outcome in OUTCOMES},
                "call_outcomes": {outcome: self.call_outcomes.get(outcome, 0) for outcome in OUTCOMES},
                "reasons": dict(sorted(self.reasons.items())),
                "calls": dict(sorted(self.calls.items())),
                "verified_names": sorted(self.verified_names),
            }


def current_run_context() -> dict[str, Any] | None:
    """Return the ambient run context, or ``None`` outside a graph.

    ``get_runtime()`` returns ``None`` when a tool is invoked outside LangGraph,
    which is exactly the "never policy-checked" case the metrics must not turn
    into a success. A telemetry failure must never break a tool call, so every
    failure mode degrades to ``None`` (unverified) instead of raising.
    """
    try:
        from langgraph.runtime import get_runtime
    except Exception:  # noqa: BLE001 - telemetry must never break a tool call
        return None
    try:
        runtime = get_runtime()
    except Exception:  # noqa: BLE001
        return None
    context = getattr(runtime, "context", None)
    return context if isinstance(context, dict) else None


def resolve_telemetry(context: dict[str, Any]) -> DiscoveryTelemetry:
    """Return this run's recorder, creating and storing it on first use."""
    existing = context.get(TELEMETRY_CONTEXT_KEY)
    if isinstance(existing, DiscoveryTelemetry):
        return existing
    telemetry = DiscoveryTelemetry()
    context[TELEMETRY_CONTEXT_KEY] = telemetry
    return telemetry


def current_telemetry() -> DiscoveryTelemetry | None:
    """Return the ambient run's recorder, or ``None`` when off-graph."""
    context = current_run_context()
    if context is None:
        return None
    return resolve_telemetry(context)


def record_search(
    telemetry: DiscoveryTelemetry | None,
    *,
    proposed: Any,
    context: Any = None,
    deferred: frozenset[str] = frozenset(),
) -> PromotionVerdict:
    """Record a ``tool_search`` proposal against the final policy outcome.

    ``proposed`` is the list ``tool_search`` is about to return in its
    ``Command``. It is *never* treated as the answer: the verdict is computed
    from the policy decision the enforcing middleware published for this step.
    ``context`` defaults to the ambient run context; off-graph there is no
    decision, so the verdict is ``unverified`` and nothing is counted.
    """
    resolved = current_run_context() if context is None else context
    verdict = evaluate_proposed_promotions(proposed, resolved, deferred=deferred)
    if telemetry is not None:
        telemetry.record_promotion(verdict)
    return verdict


def record_call(telemetry: DiscoveryTelemetry | None, *, kind: str, outcome: str, reason: str) -> None:
    """Record one catalog discovery call. Never touches the promotion counter."""
    if telemetry is not None:
        telemetry.record_call(kind=kind, outcome=outcome, reason=reason)
