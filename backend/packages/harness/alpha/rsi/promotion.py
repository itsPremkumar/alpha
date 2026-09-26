"""RSI promotion decision composer (plan WP-C2 slice C2c + Wave-4 §42/§26/§43/§54, features #10/#11/#15/#4 family).

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
6. ``cooldown``         — Wave-4, spec §42: ``alpha.rsi.cooldown.evaluate_cooldown``
                          over the real persisted stabilization-window record
                          (``source_change_id``, ``cycle_id``,
                          ``promotion_timestamp``, ``cooldown_until``),
                          evaluated BEFORE routing so a cooling candidate is
                          never routed. Real arithmetic on the module's
                          injected clock: ``never_promoted``/``ready`` pass,
                          ``cooling_down`` fails with the real remaining
                          seconds, and a corrupt/unreadable record fails
                          closed with the real error text. The record is
                          written ONLY after this decision actually promotes
                          — a failed decision never starts a cooldown.
7. ``evolution_route``  — only when gates 1–6 are all ok:
                          :func:`route_promotion_decision` (the module-level
                          invoke seam) ->
                          ``alpha.evolution.promotion_route.route_evolution_gate``
                          with ``human_approved=True`` and ``autonomous_mode``
                          pinned False (plan §5.4). When any earlier gate
                          failed, this entry discloses ``not routed: ...`` —
                          the engine is never invoked on a failed composition.

Wave-4 spec surfaces composed here (each owned by a NEW sibling module,
composed — never duplicated):

- spec §42 promotion cooldown → :mod:`alpha.rsi.cooldown`: gate 6 above plus
  the record written after a promoted decision (the store's four fields,
  injected clock, honest ``ValueError`` validation).
- spec §26/§43 immutable releases → :mod:`alpha.rsi.releases`: a fully-green
  decision materializes ``runtime_home()/rsi/releases/vNNN/`` with the §43
  manifest (``release``, ``commit``, ``parent_release``, ``candidate_id``,
  ``artifacts``, ``evaluator_manifest``) and flips the ONE active pointer
  atomically — release files first, ``current.json`` last; history is never
  overwritten, and a created-but-broken pointer fails closed rather than
  being re-created.
- spec §54 failure taxonomy → :mod:`alpha.rsi.errors`: each FAILING segment
  of ``decision.reason`` carries its code in brackets (``[RSI-E016]`` …),
  WARNING logs carry the code for real exceptions, and an unclassifiable
  failure is the designated ``RSI-E000`` — never a guessed code. Gate
  entries stay verbatim (below).

Cited-but-not-consumed surfaces (plan §8 adjudication honesty — the four
uncited-but-existing surfaces, dispositioned explicitly): the
``alpha.skills.security_static_scanner`` surface IS consumed for real by
:mod:`alpha.rsi.errors` (``StaticScanBlockedError`` → ``RSI-E008``); the
LangChain agent tools ``alpha.tools.builtins.variation_operator_tool``
(``run_variation_operator_step``), ``alpha.tools.builtins.avo_lineage_tool``
(``run_avo_variation``'s matches-or-improves lineage tree) and
``alpha.tools.builtins.self_improvement_tool`` (the async tool-ralph
subagent loop) are intentionally NOT consumed — the promotion path is
deterministic and evidence-gated, while those surfaces drive agent-run
state machines with their own persistence, so wiring either direction would
fabricate a coupling that does not exist. Cited here so their uncited status
is a disclosed decision, not an oversight.

Honesty contract (plan §5 binding, non-negotiable):

- ``promoted`` is True only when EVERY gate — including the routed engine
  verdict — is ok AND the Wave-4 materialization below succeeded. Any
  missing/failed/unverified/corrupt component means ``promoted=False`` and
  ``reason`` joins every failing gate's real text as ``<gate>: <reason>``
  with the spec §54 code appended in brackets (``[RSI-Exxx]``); a passing
  decision's ``reason`` is the evolution engine's own verdict verbatim (no
  code, no suffix — pinned exact in tests).
- Materialization (spec §26/§43 + §42) runs ONLY on a fully-green
  composition, in this order: the immutable ``releases/vNNN/`` directory +
  atomic single-pointer flip first, the §42 cooldown record second. A
  release failure therefore writes NO cooldown (a decision that did not
  materialize never opens a stabilization window); a cooldown failure after
  a flipped pointer is disclosed as a partial state in a failed
  ``cooldown_record`` entry — the release stays immutable, is never
  retro-edited, and the decision lands ``promoted=False`` with the real
  text. Every non-green path writes NOTHING to either store.
- ``simulated`` never gates; ``unverified`` is a disclosed 0.5-neutral,
  never a pass — enforced by the landed modules whose text travels verbatim.
- Every gate evaluation is wrapped: an exception fails THAT gate closed with
  ``failed closed: <Type>: <real text>`` and is logged at WARNING with the
  real text plus its §54 code (never a bare ``except``, never silence).
  Gate entries always carry the landed module's verbatim reason — §54 codes
  are appended ONLY to the composed ``decision.reason`` and to logs, so the
  C2c passthrough contract (pinned exact in tests) never bends.
- No invented confidence/score/rating/pass-rate fields exist anywhere in
  :class:`PromotionDecision` or its ``to_dict()`` output (a literal
  forbidden-key set is pinned in ``tests/test_rsi_promotion.py``); the
  decision still emits no timestamp of its own, so decisions stay fully
  deterministic — every clock read sits behind a module seam
  (``alpha.rsi.cooldown.RSI_COOLDOWN_CLOCK``,
  ``alpha.rsi.releases.RSI_RELEASE_CLOCK``, the injectable C2a/C2b clocks)
  and every timestamp written lives in the cooldown record or a release
  manifest, never in the decision payload.
- ``decide()``'s ONLY side effects are: the routed ``EvolutionEngine.gate``
  call (that real dispatch API's own ledger/status behavior) and — for a
  fully-green decision — one immutable release directory + one atomic
  ``current.json`` flip + one cooldown record, each failing closed with the
  real text if the write cannot happen. Every other path is read-only.

Scope fences (plan §5): no HTTP router or endpoint, no ``app.py``, no auth,
no feature manifest, no ``config_version``; this unit adds no
``RISK_POLICY``/risk-threshold constants — the plan §3 "human approval by
default for every risk class" substance is enforced unconditionally by gate 5
plus the ``autonomous_mode=False`` pin in the wiring, and policy constants are
deferred rather than half-shipped. (The ONE duration Wave-4 adds is spec §42's
mandated stabilization window, owned by ``alpha.rsi.cooldown`` — default
3600s, operator-visible via ``RSI_COOLDOWN_SECONDS``, a disclosed spec
requirement, not a hidden threshold.) The plan's broader input set
(kill-switch, evaluator-integrity gating, release-gate thresholds, canary
probes) is NOT part of this assigned composition — disclosed here, never
faked as present: the §43 release manifest RECORDS the real evaluator
manifest as provenance and never gates on it.

Seams: :func:`route_promotion_decision` remains this module's verdict seam
(the routing tests' stub point). The §54 wiring calls
``classify``/``classify_gate``/``annotate`` by name and the materialization
calls ``create_release``/``record_promotion`` by name, so failure-path tests
may monkeypatch THIS module's bindings exactly as they patch the routing
seam — the real functions run unstubbed on every positive path (pinned in
``tests/test_rsi_wave4.py``). ``alpha.rsi.cooldown.RSI_COOLDOWN_CLOCK``,
``alpha.rsi.releases.RSI_RELEASE_CLOCK`` and
``alpha.rsi.releases.RSI_EVALUATOR_MANIFEST_BUILDER`` are state seams (clock
/ one real memoized evaluator build), never verdict seams. Every gate runs
the REAL landed modules.
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
from alpha.rsi.cooldown import evaluate_cooldown, record_promotion
from alpha.rsi.errors import annotate, classify, classify_gate
from alpha.rsi.evidence_bundle import BUNDLES_DIR_NAME, INDEX_FILE_NAME, Bundle, verify_bundle
from alpha.rsi.holdout import holdout_gate
from alpha.rsi.lineage import EVIDENCE_KINDS, RsiLineageStore
from alpha.rsi.releases import create_release
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
#: ``cooldown`` (spec §42) sits BEFORE ``evolution_route``: while a
#: stabilization window is active the engine is never routed. A fully-green
#: decision may additionally append disclosed ``release_store`` /
#: ``cooldown_record`` entries when §26/§43 materialization fails — post-
#: promotion side-effect disclosures, never silent, and by design not part of
#: this closed composition set.
GATE_ORDER: tuple[str, ...] = (
    "lineage",
    "bundle_integrity",
    "evidence_standard",
    "holdout",
    "human_review",
    "cooldown",
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
    disclosed ``failed closed: ...`` / ``not routed: ...`` text); §54 codes
    are appended only to the composed ``reason`` below, never into these
    entries. A failing ``reason`` joins each failing entry as
    ``<gate>: <reason> [RSI-Exxx]``. This decision serializes no
    confidence, score, rating, or pass-rate field and no timestamp of its
    own.
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
        logger.warning("RSI promotion gate %r failed closed for candidate %r: %s [%s]", name, candidate_id, reason, classify(exc))
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


def _bookkeeping_failure(name: str, exc: Exception, candidate_id: str, *, note: str = "") -> dict[str, Any]:
    """One disclosed post-routing failure entry: real exception text, real §54 code, logged — never swallowed.

    ``name`` is ``release_store`` or ``cooldown_record`` (§26/§43/§42 side-effect
    disclosures appended after ``evolution_route`` when materialization fails);
    ``note`` carries honest partial-state context (e.g. which artifact DID land).
    """
    reason = f"failed closed: {type(exc).__name__}: {exc}{note}"
    logger.warning("RSI promotion %s failed closed for candidate %r: %s [%s]", name, candidate_id, reason, classify(exc))
    return {"gate": name, "ok": False, "reason": reason}


def _materialize_promotion(candidate_id: str, gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """§26/§43 release + §42 cooldown materialization for a fully-green, routed-OK decision.

    Order is honesty-driven: the immutable ``release/`` directory is created
    FIRST (spec §26: ``release/`` is the source of truth — a release exists
    before any cooldown claim can reference it), then the §42 cooldown record
    is written. A release failure therefore means NO cooldown record (cooldown
    is promoted-only); a cooldown failure after a landed release is disclosed
    as a ``cooldown_record`` entry carrying the real partial-state note. Each
    failure appends its own ``{gate, ok, reason}`` entry so the deviation is
    visible in ``gates`` — never a bare ``except: pass``.
    """
    payload = PromotionDecision(candidate_id=candidate_id, promoted=True, reason=gates[-1]["reason"], gates=list(gates)).to_dict()
    cycle_id = "unknown"  # disclosed default when no lineage record exists (same precedent as the lineage gate)
    lineage_record = RsiLineageStore().get(candidate_id)
    if lineage_record is not None and getattr(lineage_record, "cycle_id", None):
        cycle_id = lineage_record.cycle_id
    entries: list[dict[str, Any]] = []
    try:
        create_release(candidate_id, payload)
    except Exception as exc:
        # Only the release entry is appended: the cooldown record was never
        # ATTEMPTED (release-first order), so blaming the cooldown for the
        # release's exception would misattribute the failure — the note makes
        # the consequence explicit instead.
        entries.append(
            _bookkeeping_failure(
                "release_store",
                exc,
                candidate_id,
                note=" (cooldown not written: the §26/§43 release must exist before any §42 cooldown claim)",
            )
        )
        return entries
    try:
        record_promotion(source_change_id=candidate_id, cycle_id=cycle_id)
    except Exception as exc:
        entries.append(_bookkeeping_failure("cooldown_record", exc, candidate_id, note=" (release materialized: the active release/ entry above stands; cooldown state is unavailable)"))
    return entries


def decide(candidate_id: str, *, baseline: Mapping[str, Any] | None = None) -> PromotionDecision:
    """Compose every gate into one honest promotion decision.

    All of :data:`GATE_ORDER` is evaluated on every call — no short-circuit —
    so each gate contributes its REAL state and reason. Gates 1–6 (lineage
    through ``cooldown``, spec §42) run BEFORE routing: while a promotion
    cooldown is active the evolution engine is never invoked, and a failed
    gate 1–5 composition appends an explicit ``not routed: ...`` disclosure
    instead of calling the engine. Routing through the evolution dispatch
    engine happens only when gates 1–6 are all ok.

    When every gate including ``evolution_route`` is green, §26/§43 release
    materialization (``release/`` dir + pointer flip) runs FIRST, then the
    §42 cooldown record is written — both only on a fully-green promoted
    decision. A materialization failure appends a disclosed
    ``release_store``/``cooldown_record`` entry after ``evolution_route`` and
    lands ``promoted=False`` (fail-closed, never silent).

    ``promoted`` is the AND of every gate; a failing ``reason`` joins each
    failing entry as ``<gate>: <reason> [RSI-Exxx]`` with the spec §54 code
    appended to the COMPOSED reason only — gate entries stay verbatim. When
    promoted, ``reason`` carries the engine's verbatim verdict. Exceptions
    inside a gate fail only that gate, closed, with the real text (logged by
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
        _run_gate("cooldown", candidate_id, lambda: evaluate_cooldown()),  # window is store-global, not per-candidate
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
            logger.warning("RSI promotion routing failed closed for candidate %r: %s [%s]", candidate_id, routed_reason, classify(exc))
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
    if not failing:
        # Fully green INCLUDING routing: materialize §26/§43 release first,
        # then the §42 cooldown record. Any failure appends a disclosed entry
        # and flips the decision closed — a promotion whose cooldown/release
        # bookkeeping failed never reports promoted.
        gates.extend(_materialize_promotion(candidate_id, gates))
        failing = [entry for entry in gates if not entry["ok"]]
    if failing:
        # §54 codes annotate the COMPOSED reason only; each gate entry above
        # keeps its landed module's verbatim text (pinned by exact-equality
        # tests in test_rsi_promotion.py).
        reason = "; ".join(annotate(f"{entry['gate']}: {entry['reason']}", classify_gate(entry["gate"], entry["reason"])) for entry in failing)
        return PromotionDecision(candidate_id=candidate_id, promoted=False, reason=reason, gates=gates)
    return PromotionDecision(candidate_id=candidate_id, promoted=True, reason=gates[-1]["reason"], gates=gates)
