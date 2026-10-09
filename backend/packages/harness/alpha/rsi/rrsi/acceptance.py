"""Algorithm 2's non-compensatory rules: floor (S2), cost/within-band branches (S3), domain guard (S5).

This module implements the *decision* half of RRSI's selection side —
``admit()`` is lines 5–15 of Algorithm 2 for a single candidate. Leakage
screening (S1) is upstream, in :mod:`alpha.rsi.rrsi.critic`, because a leaking
candidate must never reach the evaluation whose score it would inflate.

The four rules and exactly when each governs:

``noise_floor`` (S2, always)
    ``Ŝ' ≥ S* − δ``. The floor sits *below* the best observed score by the
    noise band: it exists to stop the search **walking downhill through a
    sequence of regressions that are each individually small enough to be
    mistaken for noise**. It is not a bar to *improvement* — that is what the
    branch rules are for.
``cost_rule`` (S3a, when ``ΔS > δ``)
    ``ΔC ≤ β₀ + β₁·ΔS`` — additional inference cost must be justified by
    measurable performance improvement. Growth that is not paid for is
    refused; the allowance *grows* with the measured gain.
``within_band`` (S3b, when ``ΔS ≤ δ``)
    ``w_s·ΔS − w_c·ΔC + w_n·ν > 0`` — a candidate indistinguishable from the
    incumbent from the score alone is admissible only if it earns credit from
    lower cost and/or structural novelty. With ``w_s = 0`` (the coding
    setting) a score increase that stays inside the band **cannot by itself**
    make a candidate admissible.
``domain_guard`` (S5)
    an optional boolean supplied by the caller. Coding and agentic-workspace
    use ``g = 1``; the engineering-design guard refuses a candidate whose
    valid-output rate falls by more than 0.03 or whose no-submission rate
    rises by more than 0.02.

**Exactly one S3 branch governs any candidate.** The other is reported
``applicable=False`` with the measured ``ΔS`` that excluded it, so a report
never shows two competing verdicts for one candidate.

Binding rules:

* **Non-compensatory means every applicable rule must pass.** There is no
  weighted sum anywhere in the admission path: a large gain in one dimension
  cannot pay for a failure in another, which is precisely the behaviour
  unregularized RSI exhibits and RRSI exists to prevent.
* **``ok is None`` is a third state and is never coerced.** A rule that could
  not be evaluated blocks and says why. It is never turned into ``True``
  ("assume it passed") nor into ``False`` dressed as a judgement ("the rule
  rejected it").
* **``ΔC`` is relative and is never fabricated.** ``(Ĉ' − Ĉ_t)/Ĉ_t`` requires
  both sides to carry a measured absolute cost and a non-zero incumbent cost;
  anything else leaves ``ΔC`` unknown rather than defaulting it to ``0``,
  which would read as "cost did not change".
* **Nothing here reads a store.** Every input arrives as an explicit keyword,
  so the same rules serve the selection pipeline, a gate and a hand-written
  test with equal weight.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from alpha.rsi.rrsi.components import novelty
from alpha.rsi.rrsi.config import RrsiParams

__all__ = [
    "Admissibility",
    "Check",
    "Measurement",
    "admit",
    "cost_rule",
    "domain_guard_default",
    "floor_rule",
    "relative_cost_change",
    "score_change",
    "selected_branch",
    "within_band_rule",
    "within_band_utility",
]


@dataclass(frozen=True)
class Measurement:
    """One side's measured score and **absolute** cost.

    ``score`` and ``cost`` are ``None`` when unmeasured — never ``0.0``, which
    would read as "measured as zero" rather than "not measured".
    ``cost`` is an absolute quantity (policy tokens per trial);
    :func:`relative_cost_change` turns a pair of absolute costs into the
    ``ΔC`` the rules consume.

    ``evidence_kind`` is the label the producer stamped. Only ``"measured"``
    counts as measured: a ``"simulated"`` or ``"unverified"`` number is
    refused by :attr:`measured`, so a preview constant can never reach a rule.
    """

    score: float | None = None
    cost: float | None = None
    evidence_kind: str = "unverified"
    label: str = ""

    @property
    def measured(self) -> bool:
        """Whether ``score`` carries a real number under a ``measured`` label."""
        return self.evidence_kind == "measured" and _is_number(self.score)

    @property
    def cost_measured(self) -> bool:
        """Whether ``cost`` carries a real, non-negative absolute quantity."""
        return _is_number(self.cost) and float(self.cost) >= 0

    def note(self) -> str:
        """A short, real description of what this side actually has."""
        name = self.label or "measurement"
        if self.evidence_kind != "measured":
            return f"{name} evidence_kind={self.evidence_kind!r}"
        return f"{name} score={self.score!r} cost={self.cost!r}"


@dataclass(frozen=True)
class Check:
    """One rule's verdict.

    ``applicable`` distinguishes "this rule does not govern this candidate"
    from "this rule governs it and we could not evaluate it". Only the latter
    has ``ok = None`` *and* ``ran = False``. Collapsing the two would either
    block every candidate on a rule that was never relevant, or pass every
    candidate whose inputs were missing.
    """

    name: str
    applicable: bool
    ran: bool
    ok: bool | None
    reason: str

    @property
    def verdict(self) -> str:
        if not self.applicable:
            return "not_applicable"
        if not self.ran:
            return "not_evaluable"
        return "pass" if self.ok else "fail"

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "applicable": self.applicable, "ran": self.ran, "ok": self.ok, "verdict": self.verdict, "reason": self.reason}


@dataclass(frozen=True)
class Admissibility:
    """Lines 12–14 of Algorithm 2 for one candidate.

    ``admitted`` is ``True`` only when at least one applicable rule actually ran
    **and** every applicable rule passed. ``reasons`` holds the text of every
    rule that blocked or could not be evaluated, in rule order.
    """

    admitted: bool
    checks: tuple[Check, ...]
    reasons: tuple[str, ...]
    detail: str

    @property
    def ran(self) -> bool:
        """Whether at least one applicable rule was actually evaluated."""
        return any(check.ran for check in self.checks)

    @property
    def rules_run(self) -> int:
        return sum(1 for check in self.checks if check.ran)

    @property
    def rules_applicable(self) -> int:
        return sum(1 for check in self.checks if check.applicable)

    def check(self, name: str) -> Check:
        for item in self.checks:
            if item.name == name:
                return item
        raise KeyError(name)

    def to_dict(self) -> dict[str, object]:
        return {
            "admitted": self.admitted,
            "ran": self.ran,
            "rules_run": self.rules_run,
            "rules_applicable": self.rules_applicable,
            "checks": [item.to_dict() for item in self.checks],
            "reasons": list(self.reasons),
            "detail": self.detail,
        }


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def score_change(candidate: Measurement, incumbent: Measurement) -> float | None:
    """``ΔS = Ŝ' − Ŝ_t``; ``None`` when either side is unmeasured."""
    if not candidate.measured or not incumbent.measured:
        return None
    return float(candidate.score) - float(incumbent.score)


def relative_cost_change(candidate: Measurement, incumbent: Measurement) -> tuple[float | None, str]:
    """``ΔC = (Ĉ' − Ĉ_t) / Ĉ_t`` — the *relative* policy-token change.

    Returns ``(None, reason)`` when either cost is unmeasured or the incumbent
    cost is zero (a zero denominator would produce ``inf`` or raise, and
    neither is a number a rule may be handed).
    """
    if not incumbent.cost_measured:
        return None, f"incumbent cost is unmeasured ({incumbent.note()})"
    if not candidate.cost_measured:
        return None, f"candidate cost is unmeasured ({candidate.note()})"
    base = float(incumbent.cost)
    if base == 0:
        return None, "incumbent cost is 0, so a relative change is undefined (division by zero)"
    return (float(candidate.cost) - base) / base, f"ΔC = ({float(candidate.cost):.6g} − {base:.6g}) / {base:.6g}"


def selected_branch(delta_s: float | None, params: RrsiParams | None = None) -> str | None:
    """Which S3 branch governs a candidate: ``"cost_rule"``, ``"within_band"``, or ``None`` when ``ΔS`` is unknown."""
    params = (params or RrsiParams()).validate()
    if delta_s is None:
        return None
    return "cost_rule" if delta_s > params.noise_delta else "within_band"


def floor_rule(candidate: Measurement, best_score: float | None, params: RrsiParams | None = None) -> Check:
    """S2: ``Ŝ' ≥ S* − δ`` — the non-compensatory floor under the noise band.

    ``best_score`` is ``S*``, the best evolve-set score observed so far — a
    distinct quantity from the incumbent harness's own score, because the
    floor is what stops a *sequence* of individually-tolerable regressions
    from eroding the best result the search has ever reached.
    """
    params = (params or RrsiParams()).validate()
    if best_score is None:
        return Check("noise_floor", True, False, None, "S2 not evaluated: S* (best evolve-set score observed so far) is unknown, so the floor S* − δ cannot be formed.")
    if not _is_number(best_score):
        return Check("noise_floor", True, False, None, f"S2 not evaluated: S* is not a number (got {best_score!r}).")
    if not candidate.measured:
        return Check("noise_floor", True, False, None, f"S2 not evaluated: the candidate has no measured score ({candidate.note()}).")
    threshold = float(best_score) - params.noise_delta
    value = float(candidate.score)
    if value < threshold:
        return Check(
            "noise_floor",
            True,
            True,
            False,
            f"S2 refused: Ŝ' = {value:.6g} is below the floor S* − δ = {float(best_score):.6g} − {params.noise_delta} = {threshold:.6g}; a regression beyond the noise band cannot be accepted even when it is cheaper.",
        )
    return Check(
        "noise_floor",
        True,
        True,
        True,
        f"S2 passed: Ŝ' = {value:.6g} clears the floor S* − δ = {threshold:.6g} (S* = {float(best_score):.6g}, δ = {params.noise_delta}).",
    )


def cost_rule(delta_s: float | None, delta_c: float | None, params: RrsiParams | None = None) -> Check:
    """S3a: ``ΔC ≤ β₀ + β₁·ΔS``, applied when ``ΔS > δ``.

    Governs only gainful candidates; the caller decides that with
    :func:`selected_branch`. An unknown ``ΔS`` or ``ΔC`` leaves the rule
    unevaluable rather than passing by default.
    """
    params = (params or RrsiParams()).validate()
    if delta_s is None:
        return Check("cost_rule", True, False, None, "S3a not evaluated: ΔS is unknown (one side's score is unmeasured).")
    if delta_c is None:
        return Check("cost_rule", True, False, None, "S3a not evaluated: ΔC is unknown (relative policy-token change could not be formed).")
    allowance = params.cost_base + params.cost_slope * delta_s
    if delta_c <= allowance:
        return Check(
            "cost_rule",
            True,
            True,
            True,
            f"S3a passed: ΔC = {delta_c:+.6g} ≤ β₀ + β₁·ΔS = {params.cost_base} + {params.cost_slope}·{delta_s:+.6g} = {allowance:.6g}.",
        )
    return Check(
        "cost_rule",
        True,
        True,
        False,
        f"S3a refused: ΔC = {delta_c:+.6g} exceeds β₀ + β₁·ΔS = {params.cost_base} + {params.cost_slope}·{delta_s:+.6g} = {allowance:.6g} — the additional inference cost is not justified by the measured improvement.",
    )


def within_band_utility(delta_s: float | None, delta_c: float | None, novelty_count: int | None, params: RrsiParams | None = None) -> float | None:
    """The within-band admissibility expression ``w_s·ΔS − w_c·ΔC + w_n·ν``.

    Returns ``None`` when any input is unavailable. Callers test the sign
    themselves with :func:`within_band_rule`; this function exists so a report
    can show the actual number that was compared against zero.
    """
    params = (params or RrsiParams()).validate()
    if delta_s is None or delta_c is None or novelty_count is None:
        return None
    return params.weight_score * delta_s - params.weight_cost * delta_c + params.weight_novelty * novelty_count


def within_band_rule(delta_s: float | None, delta_c: float | None, novelty_count: int | None, params: RrsiParams | None = None) -> Check:
    """S3b: ``w_s·ΔS − w_c·ΔC + w_n·ν > 0``, applied when ``ΔS ≤ δ``.

    ``ν`` of ``None`` means the credit-assignment ledger could not be read, so
    novelty credit cannot be claimed — the rule then cannot evaluate, rather
    than treating unknown novelty as zero.
    """
    params = (params or RrsiParams()).validate()
    if delta_s is None:
        return Check("within_band", True, False, None, "S3b not evaluated: ΔS is unknown (one side's score is unmeasured).")
    if delta_c is None:
        return Check("within_band", True, False, None, "S3b not evaluated: ΔC is unknown (relative policy-token change could not be formed).")
    if novelty_count is None:
        return Check("within_band", True, False, None, "S3b not evaluated: ν (structural novelty credit) is unknown because the credit-assignment ledger could not be read.")
    utility = within_band_utility(delta_s, delta_c, novelty_count, params)
    if utility > 0:
        return Check(
            "within_band",
            True,
            True,
            True,
            f"S3b passed: w_s·ΔS − w_c·ΔC + w_n·ν = {utility:+.6g} > 0 (w_s={params.weight_score}, ΔS={delta_s:+.6g}; w_c={params.weight_cost}, ΔC={delta_c:+.6g}; w_n={params.weight_novelty}, ν={novelty_count}).",
        )
    return Check(
        "within_band",
        True,
        True,
        False,
        f"S3b refused: w_s·ΔS − w_c·ΔC + w_n·ν = {utility:+.6g} is not > 0 "
        f"(w_s={params.weight_score}, ΔS={delta_s:+.6g}; w_c={params.weight_cost}, ΔC={delta_c:+.6g}; w_n={params.weight_novelty}, ν={novelty_count}) — "
        "an indistinguishable candidate bought nothing.",
    )


def domain_guard_default(candidate: Measurement, incumbent: Measurement) -> Check:
    """S5 with no domain configured: ``g = 1`` (the paper's coding/workspace default).

    Callers in a domain with a real guard pass their own :class:`Check`; this
    default states plainly that no guard was configured instead of implying
    that one ran and passed.
    """
    return Check(
        "domain_guard",
        True,
        True,
        True,
        f"S5 passed trivially: no domain-specific guard is configured for this domain (paper: coding and agentic-workspace use g = 1). Candidate: {candidate.note()}; incumbent: {incumbent.note()}.",
    )


def attribution_guard(components: Sequence[str] | None) -> Check:
    """Alpha-side guard (not in the paper): the change must name components of ``K``.

    Kept separate from S5 and labelled as an addition, because RRSI's own
    ``ν`` and ``B_t`` both need ``comp(H')`` to be known — an unattributed
    change would silently earn zero novelty credit *and* escape structural
    pruning at the same time, which is two independent distortions for the
    price of one omission.
    """
    from alpha.rsi.rrsi.components import COMPONENTS  # local import: components imports nothing from here, but keeps this module's import surface minimal

    if not components:
        return Check("component_attribution", True, False, None, "not evaluated: the change declares no component of K, so comp(H') is unknown — ν and B_t cannot be computed for it.")
    unknown = [name for name in components if name not in COMPONENTS]
    if unknown:
        return Check("component_attribution", True, True, False, f"refused: component(s) outside K declared: {', '.join(sorted(unknown))}; K = {list(COMPONENTS)}.")
    return Check("component_attribution", True, True, True, f"passed: comp(H') = {', '.join(sorted(components))} ⊆ K.")


def admit(
    *,
    candidate: Measurement,
    incumbent: Measurement,
    best_score: float | None,
    components: Sequence[str] = (),
    winning_components: Collection[str] | None = None,
    guard: Check | None = None,
    params: RrsiParams | None = None,
) -> Admissibility:
    """Apply Algorithm 2 lines 5–14 to one candidate.

    Rule order is fixed (floor → cost branch → within-band branch → domain
    guard → attribution) so a report reads the way the paper states them and a
    reason list is stable across runs. Exactly one of the two S3 branches is
    applicable; the other records the measured ``ΔS`` that excluded it.

    ``winning_components`` is the set ``{ℓ : N_t(ℓ) = 1}`` — components that
    have appeared in a winning edit — from which ``ν`` is derived. Pass
    ``None`` when the ledger was unreadable; ``ν`` then becomes ``None`` and
    the within-band branch reports it as unevaluable rather than as zero.
    """
    params = (params or RrsiParams()).validate()
    delta_s = score_change(candidate, incumbent)
    delta_c, delta_c_note = relative_cost_change(candidate, incumbent)
    branch = selected_branch(delta_s, params)
    novel = novelty(components, winning_components)

    if branch == "cost_rule":
        cost_check = cost_rule(delta_s, delta_c, params)
        band_check = Check("within_band", False, False, None, f"S3b not applicable: ΔS = {delta_s:+.6g} > δ = {params.noise_delta}, so the gain-dependent cost rule governs this candidate.")
    elif branch == "within_band":
        cost_check = Check("cost_rule", False, False, None, f"S3a not applicable: ΔS = {delta_s:+.6g} ≤ δ = {params.noise_delta}, so the within-band rule governs this candidate.")
        band_check = within_band_rule(delta_s, delta_c, novel, params)
    else:
        cost_check = Check("cost_rule", True, False, None, "S3a not evaluated: ΔS is unknown, so no S3 branch could be selected.")
        band_check = Check("within_band", False, False, None, "S3b not selected: ΔS is unknown, so neither S3 branch could be chosen.")

    checks: list[Check] = [
        floor_rule(candidate, best_score, params),
        cost_check,
        band_check,
        guard if guard is not None else domain_guard_default(candidate, incumbent),
        attribution_guard(components),
    ]

    applicable = [item for item in checks if item.applicable]
    blocking = [item for item in applicable if item.ok is not True]
    admitted = bool(applicable) and not blocking and any(item.ran for item in applicable)

    if not applicable:
        detail = "no rule applied to this candidate"
    elif not any(item.ran for item in applicable):
        detail = "every applicable rule was unevaluable — nothing was evaluated, so nothing passed"
    elif blocking:
        detail = "; ".join(f"{item.name}: {item.reason}" for item in blocking)
    else:
        detail = f"all {len(applicable)} applicable rule(s) passed (ΔS = {'unknown' if delta_s is None else f'{delta_s:+.6g}'}, {delta_c_note})"

    return Admissibility(admitted=admitted, checks=tuple(checks), reasons=tuple(item.reason for item in blocking), detail=detail)
