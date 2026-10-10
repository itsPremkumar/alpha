"""Algorithm 2 — RRSI's selection half, plus the block-only promotion gate.

Two public entry points, deliberately separated because they answer different
questions:

:func:`select_round`
    the full round decision: screen every candidate (S1), admit the survivors
    under the non-compensatory rules (:mod:`alpha.rsi.rrsi.acceptance`), take
    ``argmax Ŝ'`` among the admitted, advance ``S*``, and emit the ``a = 1``
    records. A pure function — it reads no store and writes nothing.
:func:`election_gate_for`
    one candidate measured against durable state, shaped for use as a
    **block-only conjunct** in an existing promotion gate. Its honest
    tri-state (:class:`RrsiGateOutcome`) is the load-bearing contract: when it
    cannot assemble a complete measurement set it reports ``ran=False`` and
    names what was missing. It never converts missing evidence into a pass,
    and never into a block either.

Why the tri-state exists: this gate is additive. Alpha already has a
promotion decision that runs whether or not RRSI has ever been exercised. A
gate that blocked on absent RRSI state would fail closed on a search that was
never started, and a gate that returned ``True`` on absent state would be a
green light nobody earned. ``ran=False`` is neither — it is a disclosed
non-event, and it is what makes the conjunct safe to attach unconditionally.

Binding rules:

* **Selection is a hard conjunction, never a ranking.** ``admit()``'s
  verdict is applied as-is; no score, cost or novelty term is summed into it
  after the fact. ``argmax`` runs only *over already-admitted* candidates
  (Algorithm 2 line 16), so a candidate that failed a rule can never win by
  out-scoring one that passed.
* **The critic runs before the rules, and a leaked candidate never reaches
  evaluation.** S1 is applied first and a finding is final: a candidate with a
  leakage finding is excluded from ``A_t`` regardless of its score.
* **An empty ``A_t`` keeps the incumbent.** It is never an error and never a
  free pass for the best-scoring rejected candidate.
* **``S*`` only rises**, and starts as ``None`` rather than ``0.0`` — a
  floor measured against zero would admit everything.
* **Nothing here persists.** Round state belongs to
  :mod:`alpha.rsi.rrsi.store`; a caller that wants durability composes the two
  explicitly so a write can never be implied by a decision.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.rsi.rrsi.acceptance import Admissibility, Check, Measurement, admit
from alpha.rsi.rrsi.config import RrsiParams
from alpha.rsi.rrsi.critic import Reviewer, ScreenVerdict, screen_candidate
from alpha.rsi.rrsi.history import RrsiHistory
from alpha.rsi.rrsi.store import RrsiRoundStore

logger = logging.getLogger(__name__)

__all__ = [
    "RrsiGateOutcome",
    "RoundDecision",
    "ScoredCandidate",
    "election_gate_for",
    "select_round",
]


@dataclass(frozen=True)
class ScoredCandidate:
    """One evaluated candidate: its measurement plus everything the rules need.

    ``components`` is ``comp(H')`` — required by ``ν`` and by the attribution
    guard. ``diff_text`` is the source diff the leakage critic reads; empty
    means no diff was supplied, in which case S1 is reported as not
    applicable rather than as a pass.
    """

    candidate_id: str
    measurement: Measurement
    components: tuple[str, ...] = ()
    diff_text: str = ""
    task_text: str = ""

    @property
    def score(self) -> float | None:
        return self.measurement.score if self.measurement.measured else None


@dataclass(frozen=True)
class RoundDecision:
    """The outcome of one Algorithm 2 round.

    ``winner_id`` is ``None`` exactly when the incumbent was retained
    (``A_t`` empty) — never the id of a rejected candidate. ``accepted_ids``
    is the ``a = 1`` set: a candidate wins its round only when it is
    ``H_{t+1} ≠ H_t``, which here means ``winner_id`` is set.
    """

    winner_id: str | None
    winner_score: float | None
    incumbent_retained: bool
    admitted_ids: tuple[str, ...]
    rejected: dict[str, tuple[str, ...]]
    screens: dict[str, ScreenVerdict]
    admissibility: dict[str, Admissibility]
    best_score: float | None
    accepted_ids: tuple[str, ...]
    candidates_seen: int
    detail: str
    recorded_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "winner_id": self.winner_id,
            "winner_score": self.winner_score,
            "incumbent_retained": self.incumbent_retained,
            "admitted_ids": list(self.admitted_ids),
            "rejected": {key: list(value) for key, value in self.rejected.items()},
            "screens": {key: value.to_dict() for key, value in self.screens.items()},
            "admissibility": {key: value.to_dict() for key, value in self.admissibility.items()},
            "best_score": self.best_score,
            "accepted_ids": list(self.accepted_ids),
            "candidates_seen": self.candidates_seen,
            "detail": self.detail,
        }


def select_round(
    candidates: Sequence[ScoredCandidate],
    *,
    incumbent: Measurement,
    best_score: float | None,
    history: RrsiHistory | None = None,
    params: RrsiParams | None = None,
    guard_for: Any = None,
    reviewer: Reviewer | None = None,
    task_text: str = "",
) -> RoundDecision:
    """Run Algorithm 2 lines 1–19 over one round's candidate set.

    ``guard_for`` is an optional ``candidate -> Check`` callable supplying the
    domain guard ``g`` for that candidate; when it is ``None`` every candidate
    gets :func:`alpha.rsi.rrsi.acceptance.domain_guard_default` (``g = 1``,
    stated as such rather than as a guard that ran).

    ``history`` supplies ``N_t`` (which components have previously won), from
    which ``ν`` is derived. When it is ``None`` or unreadable, ``ν`` is
    ``None`` and a candidate whose governing branch is the within-band rule is
    reported as **unevaluable**, not as scoring zero novelty.
    """
    params = (params or RrsiParams()).validate()
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise ValueError("select_round() takes a sequence of ScoredCandidate, not a bare string.")
    winning: frozenset[str] | None = history.accepted_components() if history is not None else frozenset()

    screens: dict[str, ScreenVerdict] = {}
    admissible: dict[str, Admissibility] = {}
    rejected: dict[str, tuple[str, ...]] = {}
    admitted_ids: list[str] = []

    for candidate in candidates:
        if not isinstance(candidate, ScoredCandidate):
            raise ValueError(f"select_round() takes ScoredCandidate instances, got {type(candidate).__name__}.")
        verdict = screen_candidate(candidate.diff_text, task_text=candidate.task_text or task_text, reviewer=reviewer)
        screens[candidate.candidate_id] = verdict
        if not verdict.passed:
            rejected[candidate.candidate_id] = (f"S1 leakage screen: {verdict.reason}",)
            continue

        guard = guard_for(candidate) if callable(guard_for) else None
        verdict_rules = admit(
            candidate=candidate.measurement,
            incumbent=incumbent,
            best_score=best_score,
            components=candidate.components,
            winning_components=winning,
            guard=guard,
            params=params,
        )
        admissible[candidate.candidate_id] = verdict_rules
        if verdict_rules.admitted:
            admitted_ids.append(candidate.candidate_id)
        else:
            rejected[candidate.candidate_id] = verdict_rules.reasons or (verdict_rules.detail,)

    # Line 16: argmax Ŝ' over A_t only. A rejected candidate is not a
    # runner-up — it is absent from the maximisation entirely.
    winner: ScoredCandidate | None = None
    for candidate in candidates:
        if candidate.candidate_id not in admitted_ids:
            continue
        if winner is None or (candidate.score is not None and (winner.score is None or candidate.score > winner.score)):
            winner = candidate

    if winner is None:
        new_best = best_score
        detail = f"no candidate admitted ({len(candidates)} evaluated, {len(rejected)} rejected); incumbent retained."
        if not candidates:
            detail = "no candidates supplied; incumbent retained."
    else:
        new_best = winner.score if best_score is None or (winner.score is not None and winner.score > best_score) else best_score
        detail = f"admitted {len(admitted_ids)} of {len(candidates)} candidate(s); selected {winner.candidate_id} with Ŝ' = {winner.score!r}."

    return RoundDecision(
        winner_id=None if winner is None else winner.candidate_id,
        winner_score=None if winner is None else winner.score,
        incumbent_retained=winner is None,
        admitted_ids=tuple(admitted_ids),
        rejected=rejected,
        screens=screens,
        admissibility=admissible,
        best_score=new_best,
        accepted_ids=() if winner is None else (winner.candidate_id,),
        candidates_seen=len(candidates),
        detail=detail,
    )


# --------------------------------------------------------------------------------------
# The block-only promotion conjunct
# --------------------------------------------------------------------------------------

#: The six quantities Algorithm 2 needs before it can decide anything. Each
#: is resolved from several sources; a source that cannot supply it is
#: recorded with the real reason and the search continues to the next.
REQUIRED_SIGNALS: tuple[str, ...] = (
    "candidate_score",
    "candidate_cost",
    "incumbent_score",
    "incumbent_cost",
    "best_score",
    "components",
)


@dataclass(frozen=True)
class RrsiGateOutcome:
    """Tri-state result of the RRSI selection conjunct.

    ``status`` is one of:

    ``"blocked"``
        the gate ran and refused. ``blocking`` is ``True``.
    ``"admitted"``
        the gate ran and every applicable rule passed.
    ``"not_run"``
        the gate could not assemble a complete measurement set (or had
        nothing to compare against). ``ran`` and ``blocking`` are both
        ``False``, and ``reasons`` names each missing quantity with the real
        reason it could not be resolved.

    ``not_run`` is **not a pass**. It is a disclosed non-event: the caller
    learns the gate was silent, and the reasons say why. It is also not a
    block — failing closed on a search that was never started would punish a
    deployment for not having begun.
    """

    ran: bool
    blocking: bool
    status: str
    reasons: tuple[str, ...]
    detail: str
    checks: tuple[Check, ...] = ()
    screen: ScreenVerdict | None = None
    missing: tuple[str, ...] = ()
    basis: dict[str, str] = field(default_factory=dict)

    @property
    def admitted(self) -> bool:
        return self.status == "admitted"

    def reason_text(self) -> str:
        """A single operator-facing line, or ``""`` when the gate did not block.

        Shaped to match :meth:`alpha.evolution.promotion_route.EvidenceGateOutcome
        .reason_text` so a caller composing several gates does not need a
        different formatter per gate. A non-blocking outcome always answers
        ``""`` — there is nothing to add to the engine's own reason.
        """
        if not self.blocking:
            return ""
        detail = "; ".join(self.reasons)
        return f"RRSI selection gate: {self.status}" + (f" ({detail})" if detail else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "blocking": self.blocking,
            "status": self.status,
            "reasons": list(self.reasons),
            "detail": self.detail,
            "checks": [item.to_dict() for item in self.checks],
            "screen": None if self.screen is None else self.screen.to_dict(),
            "missing": list(self.missing),
            "basis": dict(self.basis),
        }


def _safe_bundle_dir(candidate_id: str, root: Path | None = None) -> Path | None:
    """``<root>/rsi/bundles/<candidate_id>/`` for a well-formed id, else ``None``.

    A candidate id is a directory name here, so anything containing a path
    separator or traversal segment is refused rather than sanitised — the
    bundle layout is an internal contract and there is nothing to recover by
    being lenient about who is allowed to point at a directory.
    """
    if not isinstance(candidate_id, str) or not candidate_id:
        return None
    if any(token in candidate_id for token in ("/", "\\", "..")):
        return None
    if candidate_id in (".",):
        return None
    base = root if root is not None else runtime_home()
    return base / "rsi" / "bundles" / candidate_id


def _read_bundle_payload(bundle_dir: Path | None) -> dict[str, Any]:
    """Best-effort read of the bundle items this gate uses. Missing is fine; it returns only what exists."""
    out: dict[str, Any] = {}
    if bundle_dir is None or not bundle_dir.is_dir():
        return out
    for name in ("holdout.json", "candidate_metrics.json", "hypothesis.json", "manifest.json"):
        path = bundle_dir / name
        try:
            out[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return out


def _resolve_number(candidates: Sequence[tuple[str, Any]]) -> tuple[float | None, str | None, str | None]:
    """First real number among ``(basis, value)`` pairs.

    Returns ``(value, basis, missing_reason)``. ``None`` for the reason means
    a value was found. A boolean is never accepted as a number — ``True`` is
    not a score of 1.
    """
    misses: list[str] = []
    for basis, value in candidates:
        if value is None:
            misses.append(f"{basis}: absent")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            misses.append(f"{basis}: not a number (got {type(value).__name__})")
            continue
        return float(value), basis, None
    return None, None, "; ".join(misses)


def _measured_payload_number(payload: Any, keys: Sequence[str]) -> float | None:
    """First numeric value among ``keys`` in a payload stamped ``evidence_kind='measured'``."""
    if not isinstance(payload, dict):
        return None
    if payload.get("evidence_kind") not in (None, "measured"):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def election_gate_for(
    candidate_id: str,
    baseline: Mapping[str, Any] | None = None,
    *,
    signals: Mapping[str, Any] | None = None,
    params: RrsiParams | None = None,
    bundle_dir: Path | str | None = None,
    store: RrsiRoundStore | None = None,
    history: RrsiHistory | None = None,
    reviewer: Reviewer | None = None,
) -> RrsiGateOutcome:
    """Evaluate one candidate against durable RRSI state, block-only.

    Resolution order for each required quantity is
    ``signals`` → ``baseline`` → durable state (round store / evidence bundle),
    and :attr:`RrsiGateOutcome.basis` records which source supplied each one so
    the decision is auditable.

    Design note on when this runs: a candidate must have a **complete**
    measurement set — both scores, both costs, ``S*``, and an attributed
    component set — or the gate reports ``not_run``. Partial measurements are
    not interpolated, because ``ΔC`` built from one-sided token counts, or a
    floor built against a half-remembered ``S*``, would produce a confident
    verdict out of a gap.
    """
    params = (params or RrsiParams()).validate()
    baseline = dict(baseline) if isinstance(baseline, Mapping) else {}
    signals = dict(signals) if isinstance(signals, Mapping) else {}

    resolved_store = store if store is not None else RrsiRoundStore()
    state = resolved_store.state
    bundle = _safe_bundle_dir(candidate_id) if bundle_dir is None else Path(bundle_dir)
    payloads = _read_bundle_payload(bundle)

    # --- S* (the floor's reference) -----------------------------------------------------
    best_score, best_basis, best_miss = _resolve_number(
        (
            ("signals.best_score", signals.get("best_score")),
            ("round store S*", resolved_store.best_score),
            ("baseline.best_score", baseline.get("best_score")),
        )
    )

    # --- candidate ---------------------------------------------------------------------
    cand_score, cand_score_basis, cand_score_miss = _resolve_number(
        (
            ("signals.candidate_score", signals.get("candidate_score")),
            ("baseline.candidate_score", baseline.get("candidate_score")),
            ("bundle holdout.json score", _measured_payload_number(payloads.get("holdout.json"), ("score", "value"))),
        )
    )
    cand_cost, cand_cost_basis, cand_cost_miss = _resolve_number(
        (
            ("signals.candidate_cost", signals.get("candidate_cost")),
            ("baseline.candidate_cost", baseline.get("candidate_cost")),
            ("bundle candidate_metrics.json cost", _measured_payload_number(payloads.get("candidate_metrics.json"), ("cost", "tokens", "token_cost"))),
        )
    )

    # --- incumbent ---------------------------------------------------------------------
    inc_score, inc_score_basis, inc_score_miss = _resolve_number(
        (
            ("signals.incumbent_score", signals.get("incumbent_score")),
            ("baseline.score", baseline.get("score")),
            ("round store incumbent_score", None if state is None else state.incumbent_score),
        )
    )
    inc_cost, inc_cost_basis, inc_cost_miss = _resolve_number(
        (
            ("signals.incumbent_cost", signals.get("incumbent_cost")),
            ("baseline.cost", baseline.get("cost")),
            ("round store incumbent_cost", None if state is None else state.incumbent_cost),
        )
    )

    # --- comp(H') ----------------------------------------------------------------------
    components_value: tuple[str, ...] | None = None
    components_basis: str | None = None
    components_miss: str | None = None
    raw_components: Any = signals.get("components", baseline.get("components", baseline.get("component")))
    if raw_components is None:
        hypothesis = payloads.get("hypothesis.json")
        if isinstance(hypothesis, dict):
            raw_components = hypothesis.get("target_component") or hypothesis.get("components")
    if raw_components is None:
        components_miss = "no component attribution in signals, baseline, or bundle hypothesis.json"
    elif isinstance(raw_components, str):
        components_value, components_basis = (raw_components,), "single component attribution"
    elif isinstance(raw_components, (list, tuple)) and raw_components and all(isinstance(item, str) for item in raw_components):
        components_value, components_basis = tuple(raw_components), "declared component list"
    else:
        components_miss = f"component attribution is not a non-empty list of strings (got {raw_components!r})"

    missing: list[str] = []
    for label, miss in (
        ("S* (best score observed so far)", best_miss),
        ("candidate score Ŝ'", cand_score_miss),
        ("candidate cost Ĉ'", cand_cost_miss),
        ("incumbent score Ŝ_t", inc_score_miss),
        ("incumbent cost Ĉ_t", inc_cost_miss),
        ("comp(H')", components_miss),
    ):
        if miss is not None:
            missing.append(f"{label} — {miss}")

    if missing:
        detail = "RRSI selection did not run: " + "; ".join(missing)
        logger.warning("RRSI selection gate for candidate %s not run — %s", candidate_id, detail)
        return RrsiGateOutcome(
            ran=False,
            blocking=False,
            status="not_run",
            reasons=tuple(missing),
            detail=detail,
            missing=tuple(label.split(" — ", 1)[0] for label in missing),
            basis={},
        )

    if components_value is None or None in (cand_score, cand_cost, inc_score, inc_cost, best_score):
        # Unreachable: `missing` above collects exactly these absences and we
        # returned on any of them. A defensive raise beats an `assert`, which
        # `python -O` would strip into a silent None flowing into the rules.
        raise RuntimeError("RRSI selection signal resolution violated its own contract; this is a bug in alpha.rsi.rrsi.selection.")

    assert cand_score is not None and cand_cost is not None
    assert inc_score is not None and inc_cost is not None
    assert best_score is not None and components_value is not None

    basis = {
        "S*": str(best_basis),
        "candidate_score": str(cand_score_basis),
        "candidate_cost": str(cand_cost_basis),
        "incumbent_score": str(inc_score_basis),
        "incumbent_cost": str(inc_cost_basis),
        "components": str(components_basis),
    }

    # --- S1: the critic runs before anything is judged ---------------------------------
    diff_text = signals.get("diff", baseline.get("diff", ""))
    screen: ScreenVerdict | None = None
    if isinstance(diff_text, str) and diff_text:
        screen = screen_candidate(diff_text, reviewer=reviewer)
        if not screen.passed:
            detail = f"RRSI selection blocked at S1 (leakage screen), before evaluation: {screen.reason}"
            logger.warning("RRSI selection gate blocked candidate %s: %s", candidate_id, detail)
            return RrsiGateOutcome(
                ran=True,
                blocking=True,
                status="blocked",
                reasons=(f"S1 leakage screen: {screen.reason}",),
                detail=detail,
                screen=screen,
                basis=basis,
            )

    candidate = Measurement(score=cand_score, cost=cand_cost, evidence_kind="measured", label=f"candidate {candidate_id}")
    incumbent = Measurement(score=inc_score, cost=inc_cost, evidence_kind="measured", label="incumbent H_t")

    rules = admit(
        candidate=candidate,
        incumbent=incumbent,
        best_score=best_score,
        components=components_value,
        winning_components=history.accepted_components() if history is not None else frozenset(),
        params=params,
    )

    if rules.admitted:
        detail = f"RRSI selection admitted candidate {candidate_id}: {rules.detail}"
        return RrsiGateOutcome(ran=True, blocking=False, status="admitted", reasons=(), detail=detail, checks=rules.checks, screen=screen, basis=basis)

    blocking_reasons = rules.reasons or (rules.detail,)
    detail = f"RRSI selection blocked candidate {candidate_id}: {rules.detail}"
    logger.warning("RRSI selection gate blocked candidate %s: %s", candidate_id, detail)
    return RrsiGateOutcome(ran=True, blocking=True, status="blocked", reasons=blocking_reasons, detail=detail, checks=rules.checks, screen=screen, basis=basis)
