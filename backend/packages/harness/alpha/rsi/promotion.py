"""RSI promotion decision composer (plan WP-C2 slice C2c, features #10/#11/#15/#4 family).

Implements the *promotion decision* component of plan
``references/ALPHA_RSI_IMPLEMENTATION_PLAN.md`` §3 WP-C2 for this unit's
assigned scope: compose the landed sibling gates into one honest decision and
route a fully-green composition through the existing evolution dispatch
surface. The landed siblings are composed — never duplicated:
``alpha.rsi.evidence_bundle`` (C2a), ``alpha.rsi.review`` (C2b),
``alpha.rsi.holdout`` (A3), ``alpha.rsi.lineage`` (A1), and
``alpha.evolution.promotion_route`` -> ``EvolutionEngine.gate`` (the real
dispatch API the ``GateRequest`` HTTP handler on the evolution router calls).

``decide(candidate_id, *, baseline=None) -> PromotionDecision`` evaluates an
ordered, closed gate set — :data:`GATE_ORDER` — where **every** gate runs on
**every** call and each entry carries the gate's REAL state and REAL reason:

1. ``lineage``          — ``alpha.rsi.lineage``: a durable record must exist
                          with ``evidence_kind='measured'`` (simulated can
                          never gate; unverified is a disclosed 0.5-neutral,
                          never a pass).
2. ``bundle_integrity`` — ``evidence_bundle.verify_bundle``, verbatim.
3. ``evidence_standard`` — ``Bundle.meets_evidence_standard`` (the real C2a
                          logic) over the REAL finalized ``bundle_index.json``
                          kinds for :data:`REQUIRED_MEASURED_EVIDENCE` —
                          measured-only, plan §5.6.
4. ``holdout``          — ``holdout.holdout_gate`` over the recorded
                          ``holdout.json`` payload, verbatim.
5. ``human_review``     — ``review.review_decision`` /
                          ``review.human_reviewed``: only a human
                          ``approved`` decision passes; pending, rejected,
                          corrupt, or reviewer-less states fail with the
                          review module's own reason text.
6. ``evolution_route``  — only when gates 1–5 are all ok:
                          :func:`route_promotion_decision` (the module-level
                          invoke seam) ->
                          ``alpha.evolution.promotion_route.route_evolution_gate``
                          with ``human_approved=True`` and ``autonomous_mode``
                          pinned False (plan §5.4). When any earlier gate
                          failed, this entry discloses ``not routed: ...`` —
                          the engine is never invoked on a failed composition.

Honesty contract (plan §5 binding, non-negotiable):

- ``promoted`` is True only when EVERY gate — including the routed engine
  verdict — is ok. Any missing/failed/unverified/corrupt component means
  ``promoted=False`` and ``reason`` joins every failing gate's real text as
  ``<gate>: <reason>``; a passing decision's ``reason`` is the evolution
  engine's own verdict verbatim.
- ``simulated`` never gates; ``unverified`` is a disclosed 0.5-neutral,
  never a pass — enforced by the landed modules whose text travels verbatim.
- Every gate evaluation is wrapped: an exception fails THAT gate closed with
  ``failed closed: <Type>: <real text>`` and is logged at WARNING with the
  real text (never a bare ``except``, never silence).
- No invented confidence/score/rating/pass-rate fields exist anywhere in
  :class:`PromotionDecision` or its ``to_dict()`` output (a literal
  forbidden-key set is pinned in ``tests/test_rsi_promotion.py``); this
  module also emits no timestamp, so decisions are fully deterministic —
  clocks belong to the evidence writers (injectable in C2a/C2b).
- ``decide()`` is read-only except for the routed ``EvolutionEngine.gate``
  call, whose ledger/status side effects are that real dispatch API's own
  documented behavior.

Scope fences (plan §5): no HTTP router or endpoint, no ``app.py``, no auth,
no feature manifest, no ``config_version``; this unit adds no
``RISK_POLICY``/gate constants — the plan §3 "human approval by default for
every risk class" substance is enforced unconditionally by gate 5 plus the
``autonomous_mode=False`` pin in the wiring, and policy constants are
deferred rather than half-shipped. The plan's broader input set (kill-switch,
evaluator integrity, release-gate thresholds, canary probes) is NOT part of
this assigned composition — disclosed here, never faked as present.

Seam: :func:`route_promotion_decision` is this module's single module-level
invoke seam. Tests may stub ONLY that seam (to pin honest routing); every
gate above runs the REAL landed modules.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.promotion_route import route_evolution_gate
from alpha.rsi.evidence_bundle import BUNDLES_DIR_NAME, INDEX_FILE_NAME, Bundle, verify_bundle
from alpha.rsi.holdout import holdout_gate
from alpha.rsi.lineage import EVIDENCE_KINDS, RsiLineageStore
from alpha.rsi.review import human_reviewed, review_decision

logger = logging.getLogger(__name__)

__all__ = [
    "GATE_ORDER",
    "PromotionDecision",
    "REQUIRED_MEASURED_EVIDENCE",
    "decide",
    "route_promotion_decision",
]

#: The closed gate order; a decision record always carries every entry, in
#: this order, each with its real state and reason (pinned literally in tests).
GATE_ORDER: tuple[str, ...] = (
    "lineage",
    "bundle_integrity",
    "evidence_standard",
    "holdout",
    "human_review",
    "evolution_route",
)

#: Bundle evidence whose KIND must be ``measured`` before promotion can stand
#: (plan §5.6 measured-only): the holdout, shadow, and review evidence the
#: other gates consume. A missing item, a ``simulated``/``heuristic`` kind,
#: and an ``unverified``/absent label each fail with the C2a module's own
#: reason — this constant is disclosed policy, not a hidden threshold.
REQUIRED_MEASURED_EVIDENCE: tuple[str, ...] = ("holdout.json", "shadow.json", "reviews.json")

#: Safe-id pattern (module-owned, mirroring the archive/workspace/evidence
#: bundle rule): a candidate id can never escape the bundle directory this
#: module reads.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class PromotionDecision:
    """One honest promotion decision: every gate's real state and real reason.

    ``gates`` entries are exactly ``{"gate", "ok", "reason"}`` — the shape the
    plan's ``promotion_decision.json`` fixtures use — with each reason
    carried verbatim from the landed module that produced it (or the
    disclosed ``failed closed: ...`` / ``not routed: ...`` text). This
    decision serializes no confidence, score, rating, or pass-rate field and
    no timestamp of its own.
    """

    candidate_id: str
    promoted: bool
    reason: str
    gates: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        """Plain-JSON form with exactly the honest keys (pinned in tests)."""
        return {
            "candidate_id": self.candidate_id,
            "promoted": self.promoted,
            "reason": self.reason,
            "gates": [{"gate": entry["gate"], "ok": entry["ok"], "reason": entry["reason"]} for entry in self.gates],
        }


def _require_safe_id(candidate_id: Any) -> str:
    """Return ``candidate_id`` when it is a safe single-path-component id, else ``ValueError`` with the real rule."""
    if not isinstance(candidate_id, str) or not _SAFE_ID_RE.fullmatch(candidate_id) or ".." in candidate_id:
        raise ValueError(
            f"unsafe candidate_id for a promotion decision: {candidate_id!r}; ids must match {_SAFE_ID_RE.pattern} "
            "(no path separators, no traversal — bundle reads stay inside runtime_home()/rsi/bundles/)."
        )
    return candidate_id


def _bundle_dir(candidate_id: str) -> Path:
    """``runtime_home()/rsi/bundles/<candidate_id>/`` (env resolved at call time, traversal-proof)."""
    _require_safe_id(candidate_id)
    return runtime_home() / "rsi" / BUNDLES_DIR_NAME / candidate_id


def _lineage_gate(candidate_id: str) -> tuple[bool, str]:
    """Durable lineage provenance: a real record with ``measured`` evidence, else the real reason."""
    store = RsiLineageStore()
    record = store.get(candidate_id)
    if record is None:
        reason = f"no RSI lineage record for candidate {candidate_id!r} (lookup failed — fail-closed, promotion requires provenance)"
        if store.skipped_lines:
            reason += f"; {store.skipped_lines} corrupt/partial lineage line(s) skipped in {store.lineage_path} (never repaired, never interpreted)"
        return False, reason
    if record.evidence_kind == "measured":
        return True, f"lineage record for {candidate_id!r} present: status={record.status!r}, evidence_kind='measured', parent={record.parent_id!r}"
    if record.evidence_kind == "simulated":
        return False, f"lineage record for {candidate_id!r} carries evidence_kind='simulated' — simulated evidence can never gate (plan §5.6)"
    if record.evidence_kind == "heuristic":
        return False, f"lineage record for {candidate_id!r} carries evidence_kind='heuristic' — a disclosed heuristic, not a measurement, cannot pass (plan §5.6)"
    return False, f"lineage record for {candidate_id!r} carries evidence_kind={record.evidence_kind!r} — a disclosed 0.5-neutral, never a pass (plan §5.6)"


def _bundle_index_items(candidate_id: str) -> dict[str, dict[str, Any]]:
    """Rebuild ``Bundle.items`` from the finalized ``bundle_index.json`` (its provenance source).

    Reads only; the real exception text (missing file, corrupt JSON, malformed
    shape, a kind outside the §5.6 whitelist) propagates to :func:`_run_gate`,
    which discloses and logs it. The populated items are exactly the record
    shape C2a documents — so :meth:`Bundle.meets_evidence_standard` below runs
    the REAL landed logic over the REAL recorded kinds, never over guesses.
    """
    index_path = _bundle_dir(candidate_id) / INDEX_FILE_NAME
    index = json.loads(index_path.read_text(encoding="utf-8"))  # OSError/ValueError = real text via _run_gate
    if not isinstance(index, dict) or not isinstance(index.get("files"), dict):
        raise ValueError(f"malformed bundle index at {index_path}: expected an object with a 'files' mapping, got {type(index).__name__}")
    items: dict[str, dict[str, Any]] = {}
    for name, entry in index["files"].items():
        kind = entry.get("evidence_kind") if isinstance(entry, dict) else None
        if kind not in EVIDENCE_KINDS:
            raise ValueError(f"index entry for {name!r} carries evidence_kind {kind!r} outside the §5.6 whitelist {sorted(EVIDENCE_KINDS)} (fail-closed)")
        items[name] = {"name": name, "evidence_kind": kind, "kind_source": entry.get("kind_source"), "added_at": entry.get("added_at")}
    return items


def _evidence_standard_gate(candidate_id: str) -> tuple[bool, str]:
    """Measured-only standard over the recorded kinds of :data:`REQUIRED_MEASURED_EVIDENCE` (real C2a logic)."""
    items = _bundle_index_items(candidate_id)
    bundle = Bundle(candidate_id, bundle_dir=_bundle_dir(candidate_id), clock=time.time)  # read-only instance; the clock is never used here
    bundle.items = items
    reasons: list[str] = []
    for name in REQUIRED_MEASURED_EVIDENCE:
        ok, text = bundle.meets_evidence_standard(name)
        if not ok:
            return False, text
        reasons.append(text)
    return True, "; ".join(reasons)


def _holdout_gate(candidate_id: str) -> tuple[bool, str]:
    """Recorded ``holdout.json`` payload through the REAL ``holdout_gate`` — verbatim verdict."""
    path = _bundle_dir(candidate_id) / "holdout.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"holdout evidence {path} is missing or unreadable: {exc}"
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        return False, f"holdout evidence {path} is corrupt: {exc}"
    return holdout_gate(payload)


def _human_review_gate(candidate_id: str) -> tuple[bool, str]:
    """Human review state: only ``review_decision``/``human_reviewed`` agreeing on ``approved`` passes; reason verbatim."""
    reviewed = human_reviewed(candidate_id)
    decision = review_decision(candidate_id)
    return bool(reviewed) and decision.state == "approved", decision.reason


def _run_gate(name: str, candidate_id: str, gate: Callable[[], tuple[bool, str]]) -> dict[str, Any]:
    """One decision entry from one gate: real ``(ok, reason)``, or fail-closed with the real exception text, logged."""
    try:
        ok, reason = gate()
    except Exception as exc:  # fail-closed wrapper, not silence: the real text travels AND is logged
        ok = False
        reason = f"failed closed: {type(exc).__name__}: {exc}"
        logger.warning("RSI promotion gate %r failed closed for candidate %r: %s", name, candidate_id, reason)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"internal error: promotion gate {name!r} returned an empty reason (every gate must disclose its real reason)")
    return {"gate": name, "ok": bool(ok), "reason": reason}


def route_promotion_decision(candidate_id: str, baseline: Mapping[str, Any] | None = None, *, human_approved: bool) -> tuple[bool, str]:
    """The module-level invoke seam: route a fully-green decision through the evolution dispatch surface.

    Delegates to ``alpha.evolution.promotion_route.route_evolution_gate`` — the
    real ``EvolutionEngine.gate`` API the landed ``GateRequest`` HTTP handler
    calls — whose ``autonomous_mode`` stays pinned ``False`` (plan §5.4:
    auto-promote OFF for every risk class). This module-level function is the
    ONLY place tests may stub, to pin honest routing; every gate inside
    :func:`decide` runs the real landed modules.
    """
    return route_evolution_gate(candidate_id, baseline, human_approved=human_approved)


def decide(candidate_id: str, *, baseline: Mapping[str, Any] | None = None) -> PromotionDecision:
    """Compose every gate into one honest promotion decision.

    All of :data:`GATE_ORDER` is evaluated on every call — no short-circuit —
    so each gate contributes its REAL state and reason. Routing through the
    evolution dispatch engine happens only when gates 1–5 are all ok; a
    failed composition appends an explicit ``not routed: ...`` disclosure
    instead of invoking the engine. ``promoted`` is the AND of every gate;
    ``reason`` joins each failing entry as ``<gate>: <reason>`` (or carries
    the engine's verbatim verdict when promoted). Exceptions inside a gate
    fail only that gate, closed, with the real text (logged by
    :func:`_run_gate`); an exception from routing itself fails the
    ``evolution_route`` entry closed the same way. ``baseline`` is the
    benchmark baseline for the engine's strictly-better comparison (``None``
    becomes the engine's own empty-baseline default — never a fabricated
    number).
    """
    gates: list[dict[str, Any]] = [
        _run_gate("lineage", candidate_id, lambda: _lineage_gate(candidate_id)),
        _run_gate("bundle_integrity", candidate_id, lambda: verify_bundle(candidate_id)),
        _run_gate("evidence_standard", candidate_id, lambda: _evidence_standard_gate(candidate_id)),
        _run_gate("holdout", candidate_id, lambda: _holdout_gate(candidate_id)),
        _run_gate("human_review", candidate_id, lambda: _human_review_gate(candidate_id)),
    ]
    if all(entry["ok"] for entry in gates):
        try:
            # Routing only ever runs after the human_review gate passed, so
            # human_approved=True is the review module's own verdict — never
            # a caller assertion; autonomous_mode stays pinned False in the
            # wiring (plan §5.4).
            routed_ok, routed_reason = route_promotion_decision(candidate_id, {} if baseline is None else baseline, human_approved=True)
        except Exception as exc:  # fail-closed routing: real text disclosed AND logged, never swallowed
            routed_ok = False
            routed_reason = f"failed closed: {type(exc).__name__}: {exc}"
            logger.warning("RSI promotion routing failed closed for candidate %r: %s", candidate_id, routed_reason)
        if not isinstance(routed_reason, str) or not routed_reason.strip():
            # The same no-empty-reason invariant _run_gate enforces: a decision
            # may never carry an undisclosed verdict — an engine response
            # without its real text fails the routing entry closed, loudly.
            routed_ok = False
            routed_reason = "failed closed: evolution engine returned an empty reason (every promotion decision must disclose its real reason)"
            logger.warning("RSI promotion routing failed closed for candidate %r: engine returned an empty reason", candidate_id)
        gates.append({"gate": "evolution_route", "ok": bool(routed_ok), "reason": routed_reason})
    else:
        gates.append(
            {
                "gate": "evolution_route",
                "ok": False,
                "reason": "not routed: an earlier gate failed — the evolution engine gate was not invoked (a failed composition is never routed for promotion)",
            }
        )
    failing = [entry for entry in gates if not entry["ok"]]
    if failing:
        return PromotionDecision(candidate_id=candidate_id, promoted=False, reason="; ".join(f"{entry['gate']}: {entry['reason']}" for entry in failing), gates=gates)
    return PromotionDecision(candidate_id=candidate_id, promoted=True, reason=gates[-1]["reason"], gates=gates)
